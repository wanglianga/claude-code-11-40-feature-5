from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..certificates import current_certificate
from ..config import GATE_LABELS, STATUS_LABELS
from ..db import get_db
from ..models import (
    ArchiveRecord,
    AuditEvent,
    Certificate,
    Determination,
    GateCheck,
    ProbabilityEstimate,
    ProbabilityModel,
    ReadingConflict,
    Rulebook,
    RulebookVersion,
    Station,
    StationVersion,
    SurveyRun,
    SurveyUnit,
    TrendReference,
    User,
)
from ..schemas import RunCreateIn
from ..security import get_current_user, require_roles
from ..serializers import (
    audit_out,
    certificate_out,
    determination_out,
    estimate_out,
    gate_out,
    record_out,
    rulebook_version_out,
    run_out,
    station_out,
    station_version_out,
    trend_out,
    unit_out,
)
from ..services import DomainError, build_run, rebuild_run

router = APIRouter(tags=["surveys"])


def _station_lookup(db: Session, units: list[SurveyUnit]) -> dict[int, Station]:
    ids = {u.station_id for u in units}
    if not ids:
        return {}
    return {s.id: s for s in db.execute(select(Station).where(Station.id.in_(ids))).scalars()}


def _current_certs(db: Session, run_id: int) -> dict[int, Certificate]:
    certs = db.execute(
        select(Certificate)
        .where(Certificate.run_id == run_id)
        .where(Certificate.superseded_by_id.is_(None))
    ).scalars()
    return {c.unit_id: c for c in certs}


def _estimates(db: Session, run_id: int) -> dict[int, ProbabilityEstimate]:
    """Latest estimate per unit (a re-evaluated unit may have more than one row)."""
    rows = list(
        db.execute(
            select(ProbabilityEstimate)
            .join(SurveyUnit, ProbabilityEstimate.unit_id == SurveyUnit.id)
            .where(SurveyUnit.run_id == run_id)
            .order_by(ProbabilityEstimate.id)
        ).scalars()
    )
    result: dict[int, ProbabilityEstimate] = {}
    for row in rows:
        result[row.unit_id] = row
    return result


def _get_run(db: Session, run_id: int) -> SurveyRun:
    run = db.get(SurveyRun, run_id)
    if run is None:
        raise HTTPException(404, "调查运行不存在")
    return run


def _get_unit(db: Session, unit_id: int) -> SurveyUnit:
    unit = db.get(SurveyUnit, unit_id)
    if unit is None:
        raise HTTPException(404, "调查单元不存在")
    return unit


@router.post("/runs")
def create_run(
    data: RunCreateIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("researcher", "auditor")),
):
    rulebook = db.get(RulebookVersion, data.rulebook_version_id)
    if rulebook is None:
        raise HTTPException(404, "规则册版本不存在")
    model = db.get(ProbabilityModel, data.model_id) if data.model_id else None
    if data.model_id and model is None:
        raise HTTPException(404, "概率模型不存在")
    try:
        run = build_run(
            db,
            rulebook_version=rulebook,
            model=model,
            station_ids=data.station_ids,
            label=data.label,
            actor=user.username,
        )
    except DomainError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.message) from exc
    db.commit()
    return run_out(run)


@router.post("/runs/{run_id}/rebuild")
def rebuild(
    run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("researcher", "auditor")),
):
    run = _get_run(db, run_id)
    new_run = rebuild_run(db, run, user.username)
    db.commit()
    return {"superseded_run_id": run.id, "new_run": run_out(new_run)}


@router.get("/runs/{run_id}/units")
def list_units(
    run_id: int,
    station_id: int | None = None,
    status: str | None = Query(default=None, description="证书状态过滤"),
    gate: str | None = Query(default=None, description="门槛过滤 device/time/space/reading"),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    _get_run(db, run_id)
    q = select(SurveyUnit).where(SurveyUnit.run_id == run_id)
    if station_id:
        q = q.where(SurveyUnit.station_id == station_id)
    units = list(db.execute(q.order_by(SurveyUnit.station_id, SurveyUnit.unit_date)).scalars())
    stations = _station_lookup(db, units)
    certs = _current_certs(db, run_id)
    estimates = _estimates(db, run_id)
    result = []
    for unit in units:
        cert = certs.get(unit.id)
        if status and (cert is None or cert.status != status):
            continue
        if status == "under_review" and (cert is None or cert.review_status != "under_review"):
            continue
        if gate and (unit.gate_status or {}).get(gate) not in {"pending", "fail", "restricted"}:
            continue
        data = unit_out(unit, stations.get(unit.station_id))
        data["certificate"] = certificate_out(cert) if cert else None
        data["probability"] = estimate_out(estimates[unit.id]) if unit.id in estimates else None
        result.append(data)
    return result


@router.get("/runs/{run_id}/overview")
def run_overview(run_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    run = _get_run(db, run_id)
    units = list(db.execute(select(SurveyUnit).where(SurveyUnit.run_id == run_id)).scalars())
    stations = _station_lookup(db, units)
    certs = _current_certs(db, run_id)
    estimates = _estimates(db, run_id)
    rulebook = db.get(RulebookVersion, run.rulebook_version_id)
    model = db.get(ProbabilityModel, run.model_version_id) if run.model_version_id else None

    status_counts = {key: 0 for key in STATUS_LABELS}
    gate_counts = {key: defaultdict(int) for key in GATE_LABELS}
    dates = sorted({u.unit_date for u in units})
    by_station: dict[int, list[dict]] = defaultdict(list)
    comparison_rows = []
    for unit in units:
        cert = certs.get(unit.id)
        est = estimates.get(unit.id)
        status = cert.status if cert else "pending"
        status_counts[status] = status_counts.get(status, 0) + 1
        for gate, value in (unit.gate_status or {}).items():
            gate_counts[gate][value] += 1
        station = stations.get(unit.station_id)
        by_station[unit.station_id].append(
            {
                "unit_id": unit.id,
                "date": unit.unit_date.isoformat(),
                "cert_status": status,
                "review_status": cert.review_status if cert else "normal",
                "probability": est.probability if est else None,
                "probability_threshold_pass": est.threshold_pass if est else None,
                "gate_status": unit.gate_status,
                "absence_eligible": cert.absence_eligible if cert else False,
                "trend_eligible": cert.trend_eligible if cert else False,
                "open_conflicts": unit.coverage_summary.get("open_conflicts", 0),
            }
        )
        both_pass = bool(
            cert
            and cert.absence_eligible
            and est
            and est.threshold_pass
            and cert.review_status == "normal"
        )
        rule_only = bool(cert and cert.status in {"restricted", "invalid", "pending"} and est and est.threshold_pass)
        prob_only = bool(cert and cert.absence_eligible and est and not est.threshold_pass)
        if both_pass or rule_only or prob_only:
            comparison_rows.append(
                {
                    "unit_id": unit.id,
                    "station_code": station.code if station else None,
                    "station_name": station.name if station else None,
                    "date": unit.unit_date.isoformat(),
                    "rule_status": cert.status if cert else "pending",
                    "absence_eligible": cert.absence_eligible if cert else False,
                    "probability": est.probability if est else None,
                    "interval": [est.interval_low, est.interval_high] if est else None,
                    "threshold": model.threshold if model else None,
                    "threshold_pass": est.threshold_pass if est else None,
                    "divergence": (
                        "both_support"
                        if both_pass
                        else "probability_supports_rule_blocks"
                        if rule_only
                        else "rule_supports_probability_below"
                    ),
                }
            )

    open_conflicts = db.execute(
        select(ReadingConflict)
        .where(ReadingConflict.run_id == run_id)
        .where(ReadingConflict.status == "open")
    ).scalars().all()
    refs = db.execute(
        select(TrendReference)
        .join(SurveyUnit, TrendReference.unit_id == SurveyUnit.id)
        .where(SurveyUnit.run_id == run_id)
    ).scalars().all()
    pending_ref_count = sum(1 for r in refs if r.status == "active")

    trail = (
        db.execute(
            select(AuditEvent).order_by(AuditEvent.id.desc()).limit(12)
        )
        .scalars()
        .all()
    )
    return {
        "run": run_out(run),
        "rulebook": rulebook_version_out(rulebook) if rulebook else None,
        "model": {
            "id": model.id,
            "code": model.code,
            "version": model.version,
            "threshold": model.threshold,
            "status": model.status,
            "calibration_sets": model.calibration_sets,
        }
        if model
        else None,
        "unit_count": len(units),
        "station_count": len(stations),
        "date_count": len(dates),
        "dates": [d.isoformat() for d in dates],
        "status_counts": status_counts,
        "gate_counts": {k: dict(v) for k, v in gate_counts.items()},
        "stations": [
            {
                **station_out(stations[sid]),
                "units": sorted(rows, key=lambda r: r["date"]),
            }
            for sid, rows in sorted(by_station.items())
            if sid in stations
        ],
        "comparison_rows": comparison_rows,
        "open_conflicts": len(open_conflicts),
        "active_trend_references": pending_ref_count,
        "recent_activity": [audit_out(e) for e in trail],
    }


@router.get("/units/{unit_id}")
def get_unit(unit_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    unit = _get_unit(db, unit_id)
    station = db.get(Station, unit.station_id)
    gates = list(
        db.execute(select(GateCheck).where(GateCheck.unit_id == unit.id).order_by(GateCheck.id))
        .scalars()
    )
    cert = current_certificate(db, unit.id)
    certs = list(
        db.execute(
            select(Certificate).where(Certificate.unit_id == unit.id).order_by(Certificate.version)
        ).scalars()
    )
    est = db.execute(
        select(ProbabilityEstimate)
        .where(ProbabilityEstimate.unit_id == unit.id)
        .order_by(ProbabilityEstimate.id.desc())
    ).scalars().first()
    model = db.get(ProbabilityModel, est.model_id) if est else None
    rulebook = db.get(RulebookVersion, unit.rulebook_version_id)
    rulebook_master = db.get(Rulebook, rulebook.rulebook_id) if rulebook else None
    version = db.get(StationVersion, unit.station_version_id)
    conflicts = list(
        db.execute(select(ReadingConflict).where(ReadingConflict.unit_id == unit.id)).scalars()
    )
    refs = list(
        db.execute(select(TrendReference).where(TrendReference.unit_id == unit.id)).scalars()
    )
    evidence_ids: set[int] = set()
    for gate in gates:
        for value in gate.evidence or []:
            if isinstance(value, int):
                evidence_ids.add(value)
    materials = [
        r for r in db.execute(select(ArchiveRecord).where(ArchiveRecord.id.in_(evidence_ids))).scalars()
    ]
    determinations: list[Determination] = []
    if evidence_ids:
        determinations = list(
            db.execute(
                select(Determination)
                .where(Determination.material_record_id.in_(evidence_ids))
                .order_by(Determination.id)
            ).scalars()
        )
    pending_materials = {
        m["record_id"] for gate in gates if gate.gate == "reading" for m in gate.measured.get("pending_materials", [])
    }
    conflicts_by_material: dict[int, list[dict]] = defaultdict(list)
    for conflict in conflicts:
        conflicts_by_material[conflict.material_record_id].append(
            {"id": conflict.id, "sources": conflict.sources, "status": conflict.status}
        )

    data = unit_out(unit, station)
    data["station"] = station_out(station, include_versions=True) if station else None
    data["station_version"] = station_version_out(version) if version else None
    data["rulebook"] = rulebook_version_out(rulebook, rulebook_master) if rulebook else None
    data["gates"] = [gate_out(g) for g in gates]
    data["certificate"] = certificate_out(cert) if cert else None
    data["certificate_history"] = [certificate_out(c) for c in certs]
    data["probability"] = estimate_out(est, model) if est else None
    data["conflicts"] = [
        {
            "id": c.id,
            "species_code": c.species_code,
            "sources": c.sources,
            "detail": c.detail,
            "status": c.status,
            "resolution": c.resolution,
            "material_record_id": c.material_record_id,
            "detected_at": c.detected_at.isoformat() if c.detected_at else None,
        }
        for c in conflicts
    ]
    data["trend_references"] = [trend_out(r) for r in refs]
    data["evidence"] = {
        "materials": [
            {
                **record_out(m, station_code=station.code if station else None),
                "is_pending": m.id in pending_materials,
                "determinations": [
                    determination_out(d)
                    for d in determinations
                    if d.material_record_id == m.id
                ],
                "conflicts": conflicts_by_material.get(m.id, []),
            }
            for m in sorted(materials, key=lambda r: (r.record_type, r.event_time))
        ],
        "record_count": len(materials),
        "determination_count": len(determinations),
    }
    audit = list(
        db.execute(
            select(AuditEvent)
            .where(
                ((AuditEvent.entity_type == "survey_unit") & (AuditEvent.entity_id == unit.id))
                | ((AuditEvent.entity_type == "certificate") & (AuditEvent.entity_id.in_([c.id for c in certs])))
                | (
                    (AuditEvent.entity_type == "determination")
                    & (AuditEvent.entity_id.in_([d.id for d in determinations]))
                )
            )
            .order_by(AuditEvent.id.desc())
        ).scalars()
    )
    data["audit"] = [audit_out(e) for e in audit]
    return data


@router.get("/units/{unit_id}/trace")
def unit_trace(unit_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Traceability chain: raw evidence → coverage conditions → rule/model versions → certificate → trend refs."""
    unit = _get_unit(db, unit_id)
    station = db.get(Station, unit.station_id)
    gates = list(
        db.execute(select(GateCheck).where(GateCheck.unit_id == unit.id).order_by(GateCheck.id))
        .scalars()
    )
    cert = current_certificate(db, unit.id)
    certs = list(
        db.execute(
            select(Certificate).where(Certificate.unit_id == unit.id).order_by(Certificate.version)
        ).scalars()
    )
    est = db.execute(
        select(ProbabilityEstimate)
        .where(ProbabilityEstimate.unit_id == unit.id)
        .order_by(ProbabilityEstimate.id.desc())
    ).scalars().first()
    model = db.get(ProbabilityModel, est.model_id) if est else None
    rulebook = db.get(RulebookVersion, unit.rulebook_version_id)
    master = db.get(Rulebook, rulebook.rulebook_id) if rulebook else None
    version = db.get(StationVersion, unit.station_version_id)
    refs = list(
        db.execute(select(TrendReference).where(TrendReference.unit_id == unit.id)).scalars()
    )
    evidence_ids: set[int] = set()
    for gate in gates:
        for value in gate.evidence or []:
            if isinstance(value, int):
                evidence_ids.add(value)
    materials = list(
        db.execute(
            select(ArchiveRecord)
            .where(ArchiveRecord.id.in_(evidence_ids))
            .order_by(ArchiveRecord.event_time)
        ).scalars()
    )
    dets = (
        list(
            db.execute(
                select(Determination)
                .where(Determination.material_record_id.in_(evidence_ids))
                .order_by(Determination.id)
            ).scalars()
        )
        if evidence_ids
        else []
    )
    return {
        "unit": unit_out(unit, station),
        "chain": [
            {
                "step": "raw_events",
                "label": "原始事件",
                "items": [
                    {
                        "record_id": rec.id,
                        "external_id": rec.external_id,
                        "record_type": rec.record_type,
                        "event_time": rec.event_time.isoformat(),
                        "received_time": rec.received_time.isoformat(),
                        "source_device": rec.source_device,
                        "hash": rec.payload_hash,
                        "revision": rec.revision,
                        "coord_precision_m": rec.coord_precision_m,
                    }
                    for rec in materials
                ],
                "determinations": [
                    {
                        "determination_id": d.id,
                        "material_record_id": d.material_record_id,
                        "species_code": d.species_code,
                        "verdict": d.verdict,
                        "confidence": d.confidence,
                        "revision": d.revision,
                        "status": d.status,
                    }
                    for d in dets
                ],
            },
            {
                "step": "coverage_conditions",
                "label": "覆盖条件",
                "items": [
                    {
                        "gate": g.gate,
                        "status": g.status,
                        "measured": g.measured,
                        "threshold": g.threshold,
                        "evidence_ids": g.evidence,
                    }
                    for g in gates
                ],
            },
            {
                "step": "versions",
                "label": "规则 / 模型 / 站点版本",
                "items": [
                    {
                        "kind": "rulebook",
                        "id": rulebook.id if rulebook else None,
                        "code": master.code if master else None,
                        "version_number": rulebook.version_number if rulebook else None,
                        "label": rulebook.label if rulebook else None,
                    },
                    {
                        "kind": "station_version",
                        "id": version.id if version else None,
                        "version_number": version.version_number if version else None,
                        "effective_from": version.effective_from.isoformat() if version else None,
                        "buffer_width_m": version.buffer_width_m if version else None,
                    },
                    {
                        "kind": "probability_model",
                        "id": model.id if model else None,
                        "code": model.code if model else None,
                        "version": model.version if model else None,
                        "threshold": model.threshold if model else None,
                    },
                ],
            },
            {
                "step": "certificate",
                "label": "证书",
                "items": [certificate_out(c) for c in certs],
                "current_id": cert.id if cert else None,
            },
            {
                "step": "trend_references",
                "label": "趋势引用",
                "items": [trend_out(r) for r in refs],
            },
        ],
    }


@router.get("/units/{unit_id}/records")
def unit_records(unit_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    unit = _get_unit(db, unit_id)
    station = db.get(Station, unit.station_id)
    gates = list(db.execute(select(GateCheck).where(GateCheck.unit_id == unit.id)).scalars())
    ids: set[int] = set()
    for gate in gates:
        for value in gate.evidence or []:
            if isinstance(value, int):
                ids.add(value)
    if not ids:
        return []
    records = list(
        db.execute(
            select(ArchiveRecord).where(ArchiveRecord.id.in_(ids)).order_by(ArchiveRecord.event_time)
        ).scalars()
    )
    return [record_out(r, include_revisions=True, station_code=station.code if station else None) for r in records]
