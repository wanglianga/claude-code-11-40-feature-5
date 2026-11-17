from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    ArchiveRecord,
    AuditEvent,
    Certificate,
    Determination,
    GateCheck,
    ReadingConflict,
    Station,
    SurveyUnit,
    TrendReference,
    User,
)
from ..schemas import (
    ConflictResolveIn,
    DeterminationIn,
    DeterminationReviseIn,
    RecordIngestIn,
    RecordReviseIn,
    RevokeIn,
    TrendReferenceIn,
)
from ..security import get_current_user, require_roles
from ..serializers import (
    audit_out,
    certificate_out,
    conflict_out,
    determination_out,
    record_out,
    trend_out,
)
from ..services import (
    DomainError,
    add_determination,
    create_trend_reference,
    ingest_record,
    reissue_unit_certificate,
    resolve_conflict,
    revise_determination,
    revise_record,
    revoke_trend_reference,
)

router = APIRouter(tags=["records"])


def _station_codes(db: Session, records: list[ArchiveRecord]) -> dict[int, str]:
    ids = {r.station_id for r in records}
    if not ids:
        return {}
    return {
        s.id: s.code for s in db.execute(select(Station).where(Station.id.in_(ids))).scalars()
    }


@router.get("/records")
def list_records(
    record_type: str | None = None,
    station_id: int | None = None,
    external_id: str | None = None,
    limit: int = Query(default=60, le=300),
    offset: int = 0,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    q = select(ArchiveRecord)
    if record_type:
        q = q.where(ArchiveRecord.record_type == record_type)
    if station_id:
        q = q.where(ArchiveRecord.station_id == station_id)
    if external_id:
        q = q.where(ArchiveRecord.external_id.ilike(f"%{external_id}%"))
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    records = list(
        db.execute(q.order_by(ArchiveRecord.event_time.desc()).offset(offset).limit(limit)).scalars()
    )
    codes = _station_codes(db, records)
    return {
        "total": total,
        "items": [record_out(r, station_code=codes.get(r.station_id)) for r in records],
    }


@router.post("/records")
def create_record(
    data: RecordIngestIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("researcher", "auditor")),
):
    try:
        record = ingest_record(db, data.model_dump(), user.username)
    except DomainError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.message) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, f"时间格式无效：{exc}") from exc
    db.commit()
    return record_out(record, include_revisions=True)


@router.get("/records/{record_id}")
def get_record(record_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    record = db.get(ArchiveRecord, record_id)
    if record is None:
        raise HTTPException(404, "档案记录不存在")
    station = db.get(Station, record.station_id)
    dets = db.execute(
        select(Determination)
        .where(Determination.material_record_id == record.id)
        .order_by(Determination.id)
    ).scalars()
    data = record_out(
        record, include_revisions=True, station_code=station.code if station else None
    )
    data["determinations"] = [determination_out(d) for d in dets]
    used_by = db.execute(
        select(GateCheck.unit_id)
        .where(GateCheck.evidence.contains([record.id]))
        .distinct()
    ).scalars()
    data["used_by_units"] = sorted(set(used_by))
    return data


@router.post("/records/{record_id}/revisions")
def revise(
    record_id: int,
    data: RecordReviseIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("researcher", "auditor")),
):
    try:
        record, previous_payload = revise_record(
            db, record_id, data.payload, data.reason, user.username
        )
    except DomainError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.message) from exc
    db.commit()
    data_out = record_out(record, include_revisions=True)
    data_out["previous_payload"] = previous_payload
    return data_out


@router.post("/determinations")
def create_determination(
    data: DeterminationIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("researcher", "auditor")),
):
    try:
        det, units = add_determination(db, actor=user.username, **data.model_dump())
    except DomainError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.message) from exc
    db.commit()
    return {
        "determination": determination_out(det),
        "affected_units": [u.id for u in units],
    }


@router.post("/determinations/{determination_id}/revisions")
def revise_det(
    determination_id: int,
    data: DeterminationReviseIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("researcher", "auditor")),
):
    try:
        new_det, units = revise_determination(
            db,
            determination_id,
            verdict=data.verdict,
            confidence=data.confidence,
            reason=data.reason,
            actor=user.username,
        )
    except DomainError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.message) from exc
    db.commit()
    certs = []
    for unit in units:
        certs.append(certificate_out(_current_cert(db, unit.id)))
    return {
        "determination": determination_out(new_det),
        "units_under_review": [u.id for u in units],
        "certificates": certs,
    }


def _current_cert(db: Session, unit_id: int) -> Certificate:
    from ..certificates import current_certificate

    cert = current_certificate(db, unit_id)
    if cert is None:
        raise HTTPException(409, "该单元无证书")
    return cert


@router.post("/units/{unit_id}/certificates/reissue")
def reissue(
    unit_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("researcher", "auditor")),
):
    unit = db.get(SurveyUnit, unit_id)
    if unit is None:
        raise HTTPException(404, "调查单元不存在")
    try:
        cert = reissue_unit_certificate(db, unit, user.username)
    except DomainError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.message) from exc
    db.commit()
    return certificate_out(cert)


@router.get("/conflicts")
def list_conflicts(
    status: str | None = None,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    q = select(ReadingConflict)
    if status:
        q = q.where(ReadingConflict.status == status)
    conflicts = list(db.execute(q.order_by(ReadingConflict.id.desc())).scalars())
    result = []
    for conflict in conflicts:
        data = conflict_out(conflict)
        material = db.get(ArchiveRecord, conflict.material_record_id)
        station = db.get(Station, conflict.station_id)
        unit = db.get(SurveyUnit, conflict.unit_id)
        data["material_external_id"] = material.external_id if material else None
        data["station_code"] = station.code if station else None
        data["station_name"] = station.name if station else None
        data["unit_date"] = unit.unit_date.isoformat() if unit else None
        result.append(data)
    return result


@router.post("/conflicts/{conflict_id}/resolve")
def resolve(
    conflict_id: int,
    data: ConflictResolveIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("researcher", "auditor")),
):
    conflict = db.get(ReadingConflict, conflict_id)
    if conflict is None:
        raise HTTPException(404, "冲突不存在")
    if conflict.status == "resolved":
        raise HTTPException(409, "该冲突已处置")
    resolve_conflict(db, conflict, data.resolution, user.username)
    db.commit()
    return conflict_out(conflict)


@router.get("/trend-references")
def list_refs(
    status: str | None = None,
    purpose: str | None = None,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    q = select(TrendReference)
    if status:
        q = q.where(TrendReference.status == status)
    if purpose:
        q = q.where(TrendReference.purpose == purpose)
    refs = list(db.execute(q.order_by(TrendReference.id.desc())).scalars())
    result = []
    for ref in refs:
        data = trend_out(ref)
        unit = db.get(SurveyUnit, ref.unit_id)
        station = db.get(Station, unit.station_id) if unit else None
        data["station_code"] = station.code if station else None
        data["station_name"] = station.name if station else None
        data["unit_date"] = unit.unit_date.isoformat() if unit else None
        result.append(data)
    return result


@router.post("/units/{unit_id}/trend-references")
def add_ref(
    unit_id: int,
    data: TrendReferenceIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("researcher", "auditor")),
):
    try:
        ref = create_trend_reference(
            db, unit_id=unit_id, purpose=data.purpose, actor=user.username, note=data.note
        )
    except DomainError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.message) from exc
    db.commit()
    return trend_out(ref)


@router.post("/trend-references/{ref_id}/revoke")
def revoke(
    ref_id: int,
    data: RevokeIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("researcher", "auditor")),
):
    ref = db.get(TrendReference, ref_id)
    if ref is None:
        raise HTTPException(404, "趋势引用不存在")
    if ref.status != "active":
        raise HTTPException(409, "仅活动引用可撤回")
    revoke_trend_reference(db, ref, data.reason, user.username)
    db.commit()
    return trend_out(ref)


@router.get("/audit")
def list_audit(
    entity_type: str | None = None,
    limit: int = Query(default=80, le=400),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    q = select(AuditEvent)
    if entity_type:
        q = q.where(AuditEvent.entity_type == entity_type)
    events = list(db.execute(q.order_by(AuditEvent.id.desc()).limit(limit)).scalars())
    return [audit_out(e) for e in events]
