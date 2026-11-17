"""Application services: run assembly, revisions, conflicts and traceability."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .certificates import current_certificate, issue_certificate, mark_under_review
from .config import STATUS_LABELS
from .coverage import CoverageAssembler, persist_unit
from .models import (
    ArchiveRecord,
    AuditEvent,
    Certificate,
    Determination,
    GateCheck,
    ProbabilityEstimate,
    ProbabilityModel,
    ReadingConflict,
    RecordRevision,
    RulebookVersion,
    Station,
    SurveyRun,
    SurveyUnit,
    TrendReference,
    utcnow,
)
from .probability import estimate as probability_estimate
from .utils import canonical_hash, local_date, parse_dt


class DomainError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _next_run_code(db: Session, season_code: str) -> str:
    count = db.execute(
        select(func.count()).select_from(SurveyRun).where(SurveyRun.season_code == season_code)
    ).scalar_one()
    return f"RUN-{season_code}-{count + 1:02d}"


def build_run(
    db: Session,
    *,
    rulebook_version: RulebookVersion,
    model: ProbabilityModel | None,
    station_ids: list[int] | None,
    label: str,
    actor: str,
) -> SurveyRun:
    q = select(Station).where(Station.status == "active")
    if station_ids:
        q = q.where(Station.id.in_(station_ids))
    stations = list(db.execute(q.order_by(Station.id)).scalars())
    if not stations:
        raise DomainError("没有可组装的站点", 404)

    run = SurveyRun(
        run_code=_next_run_code(db, rulebook_version.season_code),
        label=label,
        season_code=rulebook_version.season_code,
        season_start=rulebook_version.season_start,
        season_end=rulebook_version.season_end,
        status="active",
        rulebook_version_id=rulebook_version.id,
        model_version_id=model.id if model else None,
        created_by=actor,
    )
    db.add(run)
    db.flush()

    assembler = CoverageAssembler(db, rulebook_version, stations, model.id if model else None)
    units: list[SurveyUnit] = []
    for station in stations:
        for unit_date in assembler.season_dates():
            built = assembler.build_unit_features(station, unit_date)
            unit = persist_unit(db, run.id, built, rulebook_version, model.id if model else None)
            units.append(unit)
            unit._built = built  # type: ignore[attr-defined]

    db.flush()
    _finalize_run(db, run, units, model)
    db.add(
        AuditEvent(
            entity_type="survey_run",
            entity_id=run.id,
            action="run.assembled",
            actor=actor,
            detail={
                "stations": [s.code for s in stations],
                "units": len(units),
                "rulebook_version_id": rulebook_version.id,
                "model_id": model.id if model else None,
            },
        )
    )
    return run


def _finalize_run(
    db: Session, run: SurveyRun, units: list[SurveyUnit], model: ProbabilityModel | None
) -> None:
    for index, unit in enumerate(units, start=1):
        built = getattr(unit, "_built", None)
        gates = list(
            db.execute(select(GateCheck).where(GateCheck.unit_id == unit.id).order_by(GateCheck.id))
            .scalars()
        )
        if model is not None:
            est = probability_estimate(model, unit.feature_vector)
            db.add(
                ProbabilityEstimate(
                    unit_id=unit.id,
                    model_id=model.id,
                    probability=est["probability"],
                    interval_low=est["interval_low"],
                    interval_high=est["interval_high"],
                    threshold_pass=est["threshold_pass"],
                    conclusion=est["conclusion"],
                    variants=est["variants"],
                    assumptions=est["assumptions"],
                )
            )
        conclusions = None
        if built:
            conclusions = _certificate_conclusions(built, model)
        cert = issue_certificate(db, run, unit, gates, index, conclusions=conclusions)

        reading = next((g for g in gates if g.gate == "reading"), None)
        for conflict in (reading.measured.get("conflicts", []) if reading else []):
            material = db.get(ArchiveRecord, conflict["material_record_id"])
            db.add(
                ReadingConflict(
                    unit_id=unit.id,
                    run_id=run.id,
                    material_record_id=conflict["material_record_id"],
                    station_id=unit.station_id,
                    zone_id=unit.zone_id,
                    species_code=conflict["species_code"],
                    sources=conflict["sources"],
                    detail=(
                        f"素材 {material.external_id if material else conflict['material_external_id']} 的"
                        f"两个来源对 {conflict['species_code']} 给出互斥判读且均低于置信门槛，保留冲突。"
                    ),
                    status="open",
                )
            )
        db.add(
            AuditEvent(
                entity_type="certificate",
                entity_id=cert.id,
                action="certificate.issued",
                actor=run.created_by,
                detail={"status": cert.status, "unit_id": unit.id, "run_id": run.id},
            )
        )
    db.flush()


def _certificate_conclusions(built: dict[str, Any], model: ProbabilityModel | None) -> dict[str, Any]:
    gate_status = {g["gate"]: g["status"] for g in built["gates"]}
    reading = next(g for g in built["gates"] if g["gate"] == "reading")
    conflicts = reading["measured"].get("open_conflicts", 0)
    pending = reading["measured"].get("pending_count", 0)
    target_present = reading["measured"].get("target_present", 0)
    target_absent = reading["measured"].get("target_absent", 0)

    absence_supported = all(v == "pass" for v in gate_status.values()) and conflicts == 0 and pending == 0
    if conflicts:
        species_note = "判读冲突未决，不作已发现或未发现结论"
    elif target_present > 0:
        species_note = "目标物种已记录（已确认出现），不适用未发现结论"
    elif absence_supported:
        species_note = "调查证据充分且无目标物种确认记录，可支持该单元/物种的未发现结论"
    else:
        species_note = "调查证据不足或受限，不得简化为物种未出现"

    return {
        "absence_analysis": {
            "supported": bool(absence_supported and target_present == 0),
            "species_note": species_note,
            "confirmed_present": target_present,
            "confirmed_absent_readings": target_absent,
            "pending_materials": pending,
            "open_conflicts": conflicts,
            "never_implies_absence": ["相机停机", "路线边缘经过", "素材待判读", "时间或坐标不可信"],
        },
        "trend": {
            "rule_certificate_only": True,
            "probability_view": (
                "see_probability_estimates" if model is not None else "model_not_attached"
            ),
            "warning": "概率结果为模型推算，不是已确认的实地覆盖；正式未发现结论只使用规则证书。",
        },
        "uses": ["未发现分析", "趋势研究", "监管解释"],
    }


def rebuild_run(db: Session, run: SurveyRun, actor: str) -> SurveyRun:
    """Assemble a new run version from the same rulebook/model, superseding the old run."""
    rulebook_version = db.get(RulebookVersion, run.rulebook_version_id)
    model = db.get(ProbabilityModel, run.model_version_id) if run.model_version_id else None
    station_ids = sorted({u.station_id for u in run.units})
    new_run = build_run(
        db,
        rulebook_version=rulebook_version,
        model=model,
        station_ids=station_ids,
        label=f"{run.label}（重算）",
        actor=actor,
    )
    run.status = "superseded"
    db.add(
        AuditEvent(
            entity_type="survey_run",
            entity_id=run.id,
            action="run.superseded",
            actor=actor,
            detail={"superseded_by": new_run.id},
        )
    )
    return new_run


# --------------------------------------------------------------------- records
def ingest_record(db: Session, data: dict[str, Any], actor: str) -> ArchiveRecord:
    station = db.get(Station, int(data["station_id"]))
    if station is None:
        raise DomainError("站点不存在", 404)
    event_time = parse_dt(data["event_time"])
    received_time = parse_dt(data["received_time"])
    payload = data.get("payload") or {}
    record = ArchiveRecord(
        record_type=data["record_type"],
        external_id=data["external_id"],
        station_id=station.id,
        zone_id=station.zone_id,
        station_version_id=None,
        event_time=event_time,
        received_time=received_time,
        source_device=data["source_device"],
        device_type=data.get("device_type", "camera"),
        latitude=data.get("latitude"),
        longitude=data.get("longitude"),
        coord_precision_m=data.get("coord_precision_m"),
        payload=payload,
        payload_hash=canonical_hash(payload),
        revision=1,
        record_status="active",
        created_by=actor,
    )
    db.add(record)
    db.flush()
    db.add(
        RecordRevision(
            record_id=record.id,
            revision_number=1,
            payload=payload,
            payload_hash=record.payload_hash,
            received_time=received_time,
            changed_by=actor,
            change_reason="原始载荷入库",
        )
    )
    return record


def revise_record(
    db: Session, record_id: int, payload: dict[str, Any], reason: str, actor: str
) -> tuple[ArchiveRecord, dict[str, Any]]:
    record = db.get(ArchiveRecord, record_id)
    if record is None:
        raise DomainError("档案记录不存在", 404)
    previous = record.payload
    new_hash = canonical_hash(payload)
    record.revision += 1
    record.payload = payload
    record.payload_hash = new_hash
    if payload.get("start") and record.record_type == "downtime_log":
        record.event_time = parse_dt(payload["start"])
    db.add(
        RecordRevision(
            record_id=record.id,
            revision_number=record.revision,
            payload=payload,
            payload_hash=new_hash,
            received_time=utcnow(),
            changed_by=actor,
            change_reason=reason,
        )
    )
    db.add(
        AuditEvent(
            entity_type="archive_record",
            entity_id=record.id,
            action="record.revised",
            actor=actor,
            detail={"revision": record.revision, "reason": reason},
        )
    )
    return record, previous


def add_determination(
    db: Session,
    *,
    material_record_id: int,
    source: str,
    species_code: str,
    verdict: str,
    confidence: float,
    analyst: str,
    notes: str,
    actor: str,
) -> tuple[Determination, list[SurveyUnit]]:
    material = db.get(ArchiveRecord, material_record_id)
    if material is None:
        raise DomainError("素材记录不存在", 404)
    if verdict not in {"present", "absent", "uncertain"}:
        raise DomainError("判读结论必须是 present/absent/uncertain")
    det = Determination(
        material_record_id=material.id,
        source=source,
        species_code=species_code,
        verdict=verdict,
        confidence=confidence,
        analyst=analyst,
        revision=1,
        status="active",
        notes=notes,
    )
    db.add(det)
    db.flush()
    unit_date = local_date(_trusted_event_time(db, material))
    affected = list(
        db.execute(
            select(SurveyUnit)
            .where(SurveyUnit.station_id == material.station_id)
            .where(SurveyUnit.unit_date == unit_date)
            .order_by(SurveyUnit.id.desc())
        )
        .scalars()
    )
    return det, affected


def revise_determination(
    db: Session,
    determination_id: int,
    *,
    verdict: str,
    confidence: float,
    reason: str,
    actor: str,
) -> tuple[Determination, list[SurveyUnit]]:
    previous = db.get(Determination, determination_id)
    if previous is None:
        raise DomainError("判读记录不存在", 404)
    if previous.status != "active":
        raise DomainError("该判读已被替代，不能再次修订")
    material = db.get(ArchiveRecord, previous.material_record_id)
    if material is None:
        raise DomainError("素材记录不存在", 404)
    new = Determination(
        material_record_id=previous.material_record_id,
        source=previous.source,
        species_code=previous.species_code,
        verdict=verdict,
        confidence=confidence,
        analyst=previous.analyst,
        revision=previous.revision + 1,
        status="active",
        notes=reason,
    )
    db.add(new)
    db.flush()
    previous.status = "superseded"
    previous.superseded_by_id = new.id
    unit_date = local_date(_trusted_event_time(db, material))
    affected = list(
        db.execute(
            select(SurveyUnit)
            .where(SurveyUnit.station_id == material.station_id)
            .where(SurveyUnit.unit_date == unit_date)
            .order_by(SurveyUnit.id.desc())
        )
        .scalars()
    )
    refreshed: list[SurveyUnit] = []
    for unit in affected:
        if unit.rulebook_version_id is None:
            continue
        rulebook_version = db.get(RulebookVersion, unit.rulebook_version_id)
        previous_gate = db.execute(
            select(GateCheck).where(GateCheck.unit_id == unit.id).where(GateCheck.gate == "reading")
        ).scalar_one_or_none()
        previous_status = previous_gate.status if previous_gate else None
        previous_present = int((previous_gate.measured or {}).get("target_present", 0)) if previous_gate else 0
        new_built = _reassemble_unit(db, unit, rulebook_version)
        if new_built is None:
            continue
        new_reading = next(g for g in new_built["gates"] if g["gate"] == "reading")
        new_present = int(new_reading["measured"].get("target_present", 0))
        presence_changed = new_present != previous_present
        changed = presence_changed or new_reading["status"] != previous_status
        if changed:
            model = (
                db.get(ProbabilityModel, unit.model_version_id) if unit.model_version_id else None
            )
            mark_under_review(
                db,
                [unit.id],
                f"判读修订（判读 {previous.id} → {new.id}，{reason}）改变了目标物种出现判定，"
                "当前证书复审中，暂停新趋势引用；旧运行保留原证书。",
                actor,
                conclusions_by_unit={unit.id: _certificate_conclusions(new_built, model)},
            )
            refreshed.append(unit)
        db.add(
            AuditEvent(
                entity_type="determination",
                entity_id=new.id,
                action="determination.revised",
                actor=actor,
                detail={
                    "previous_id": previous.id,
                    "unit_id": unit.id,
                    "reading_status_before": previous_status,
                    "reading_status_after": new_reading["status"],
                    "target_present_before": previous_present,
                    "target_present_after": new_present,
                    "review_triggered": changed,
                    "reason": reason,
                },
            )
        )
    return new, refreshed


def _reassemble_unit(
    db: Session, unit: SurveyUnit, rulebook_version: RulebookVersion
) -> dict[str, Any] | None:
    station = db.get(Station, unit.station_id)
    if station is None:
        return None
    assembler = CoverageAssembler(db, rulebook_version, [station], unit.model_version_id)
    try:
        built = assembler.build_unit_features(station, unit.unit_date)
    except ValueError:
        return None
    unit.coverage_summary = built["coverage_summary"]
    unit.feature_vector = built["feature_vector"]
    unit.gate_status = {g["gate"]: g["status"] for g in built["gates"]}
    unit.data_issues = built["issues"]
    unit.assembled_at = utcnow()
    existing = list(
        db.execute(
            select(GateCheck).where(GateCheck.unit_id == unit.id).order_by(GateCheck.id)
        ).scalars()
    )
    evaluation_round = max((g.evaluation_round for g in existing), default=0) + 1
    for gate in existing:
        db.delete(gate)
    db.flush()
    for gate in built["gates"]:
        db.add(
            GateCheck(
                unit_id=unit.id,
                gate=gate["gate"],
                requirement_code=gate["requirement_code"],
                evaluation_round=evaluation_round,
                status=gate["status"],
                measured=gate["measured"],
                threshold=gate["threshold"],
                explanation=gate["explanation"],
                rule_ref=gate["rule_ref"],
                evidence=gate["evidence"],
            )
        )
    db.flush()
    return built


def _trusted_event_time(db: Session, record: ArchiveRecord) -> datetime:
    """Event time after applying a verifiable calibration for the same device, if any."""
    calibrations = list(
        db.execute(
            select(ArchiveRecord)
            .where(ArchiveRecord.station_id == record.station_id)
            .where(ArchiveRecord.record_type == "time_calibration")
        ).scalars()
    )
    event_time = record.event_time
    best: tuple[ArchiveRecord, float] | None = None
    for cal in calibrations:
        payload = cal.payload
        if payload.get("target_device") and payload["target_device"] != record.source_device:
            continue
        if not payload.get("verified", False):
            continue
        valid_from = payload.get("valid_from")
        valid_to = payload.get("valid_to")
        if valid_from and event_time < parse_dt(valid_from):
            continue
        if valid_to and event_time > parse_dt(valid_to):
            continue
        offset = abs(float(payload.get("offset_seconds", 0.0)))
        if best is None or offset > best[1]:
            best = (cal, offset)
    if best is None:
        return event_time
    return event_time - timedelta(seconds=float(best[0].payload["offset_seconds"]))


# ----------------------------------------------------------------- trend refs
def create_trend_reference(
    db: Session, *, unit_id: int, purpose: str, actor: str, note: str
) -> TrendReference:
    if purpose not in {"absence", "trend"}:
        raise DomainError("用途只能是 absence 或 trend")
    unit = db.get(SurveyUnit, unit_id)
    if unit is None:
        raise DomainError("调查单元不存在", 404)
    cert = current_certificate(db, unit_id)
    if cert is None:
        raise DomainError("该单元尚无证书记录", 409)
    if cert.review_status == "under_review":
        raise DomainError("当前证书复审中，暂停新趋势引用", 409)
    estimate_row = db.execute(
        select(ProbabilityEstimate)
        .where(ProbabilityEstimate.unit_id == unit_id)
        .order_by(ProbabilityEstimate.id.desc())
    ).scalars().first()
    if purpose == "absence":
        if not cert.absence_eligible:
            raise DomainError(
                f"证书 {cert.certificate_no} 状态为 {STATUS_LABELS.get(cert.status, cert.status)}，"
                "不可用于正式未发现结论",
                409,
            )
    else:
        if not cert.trend_eligible:
            raise DomainError(
                f"证书 {cert.certificate_no} 当前不可用于趋势输入", 409
            )
    count = db.execute(select(func.count()).select_from(TrendReference)).scalar_one()
    ref = TrendReference(
        reference_code=f"TR-{purpose[:3].upper()}-{date.today():%Y%m}-{count + 1:03d}",
        purpose=purpose,
        unit_id=unit_id,
        certificate_id=cert.id,
        probability_estimate_id=estimate_row.id if estimate_row else None,
        status="active",
        snapshot={
            "certificate_no": cert.certificate_no,
            "certificate_status": cert.status,
            "review_status": cert.review_status,
            "absence_eligible": cert.absence_eligible,
            "trend_eligible": cert.trend_eligible,
            "gate_status": unit.gate_status,
            "rulebook_version_id": unit.rulebook_version_id,
            "station_version_id": unit.station_version_id,
            "model_version_id": unit.model_version_id,
            "probability": estimate_row.probability if estimate_row else None,
            "interval": (
                [estimate_row.interval_low, estimate_row.interval_high] if estimate_row else None
            ),
        },
        created_by=actor,
        note=note,
    )
    db.add(ref)
    db.flush()
    db.add(
        AuditEvent(
            entity_type="trend_reference",
            entity_id=ref.id,
            action="trend_reference.created",
            actor=actor,
            detail={"unit_id": unit_id, "purpose": purpose, "certificate_id": cert.id},
        )
    )
    return ref


def revoke_trend_reference(db: Session, ref: TrendReference, reason: str, actor: str) -> None:
    ref.status = "revoked"
    ref.suspended_reason = reason
    ref.suspended_at = utcnow()
    db.add(
        AuditEvent(
            entity_type="trend_reference",
            entity_id=ref.id,
            action="trend_reference.revoked",
            actor=actor,
            detail={"reason": reason},
        )
    )


def resolve_conflict(
    db: Session, conflict: ReadingConflict, resolution: str, actor: str
) -> None:
    conflict.status = "resolved"
    conflict.resolution = resolution
    conflict.resolved_at = utcnow()
    db.add(
        AuditEvent(
            entity_type="reading_conflict",
            entity_id=conflict.id,
            action="reading_conflict.resolved",
            actor=actor,
            detail={"resolution": resolution},
        )
    )


def reissue_unit_certificate(db: Session, unit: SurveyUnit, actor: str) -> Certificate:
    rulebook_version = db.get(RulebookVersion, unit.rulebook_version_id)
    model = db.get(ProbabilityModel, unit.model_version_id) if unit.model_version_id else None
    built = _reassemble_unit(db, unit, rulebook_version)
    if built is None:
        raise DomainError("无法重新组装该调查单元", 409)
    gates = list(
        db.execute(select(GateCheck).where(GateCheck.unit_id == unit.id).order_by(GateCheck.id))
        .scalars()
    )
    current = current_certificate(db, unit.id)
    next_version = (current.version + 1) if current else 1
    cert = issue_certificate(
        db,
        unit.run,
        unit,
        gates,
        next_version,
        conclusions=_certificate_conclusions(built, model),
        version=next_version,
    )
    if current:
        current.superseded_by_id = cert.id
    db.add(
        AuditEvent(
            entity_type="certificate",
            entity_id=cert.id,
            action="certificate.reissued",
            actor=actor,
            detail={"unit_id": unit.id, "previous": current.id if current else None},
        )
    )
    return cert
