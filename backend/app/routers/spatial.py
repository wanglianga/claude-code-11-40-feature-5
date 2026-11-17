from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    DatasetRevision,
    PartitionRegion,
    PublicMaterial,
    PublicationCheck,
    PublicationRun,
    RecipientScope,
    SpeciesSensitivity,
    SurveyRun,
    User,
    utcnow,
)
from ..security import get_current_user, require_roles
from ..spatial import PublicationError, create_publication
from ..spatial_datasets import (
    DATASETS,
    SENSITIVITY_BASELINE,
    SENSITIVITY_LABELS,
    activate,
    active_revision,
)
from ..spatial_delivery import (
    DeliveryError,
    active_material,
    build_public_payload,
    compare_publications,
    internal_report,
    materials_out,
    publication_out,
    publish,
    region_internal_out,
    withdraw,
)

router = APIRouter(prefix="/spatial", tags=["spatial-internal"])
public_router = APIRouter(prefix="/public", tags=["spatial-public"])


class ActivateIn(BaseModel):
    version: int = Field(ge=1)
    note: str = ""


class PublicationIn(BaseModel):
    species_code: str
    scope_code: str
    run_id: int
    note: str = ""


class WithdrawIn(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


def _raise(exc: Exception) -> None:
    if isinstance(exc, PublicationError):
        raise HTTPException(exc.status_code, exc.message) from exc
    if isinstance(exc, DeliveryError):
        raise HTTPException(exc.status_code, exc.message) from exc
    raise exc


# ------------------------------------------------------------------ datasets
@router.get("/datasets")
def list_datasets(db: Session = Depends(get_db), _=Depends(get_current_user)):
    revisions = list(
        db.execute(select(DatasetRevision).order_by(DatasetRevision.dataset, DatasetRevision.version))
        .scalars()
    )
    scopes = list(db.execute(select(RecipientScope).order_by(RecipientScope.id)).scalars())
    sensitivities = list(
        db.execute(select(SpeciesSensitivity).order_by(SpeciesSensitivity.dataset_version))
        .scalars()
    )
    return {
        "revisions": [
            {
                "id": row.id,
                "dataset": row.dataset,
                "version": row.version,
                "active": row.active,
                "fingerprint": row.fingerprint,
                "source_file": row.source_file,
                "notes": row.notes,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in revisions
        ],
        "scopes": [
            {
                "code": scope.code,
                "name": scope.name,
                "audience": scope.audience,
                "purpose": scope.purpose,
                "external": scope.external,
                "allowed_levels": scope.allowed_levels,
                "thresholds": scope.thresholds,
                "dataset_version": scope.dataset_version,
            }
            for scope in scopes
        ],
        "sensitivity_baseline": SENSITIVITY_BASELINE,
        "sensitivity_labels": SENSITIVITY_LABELS,
        "sensitivity": [
            {
                "species_code": row.species_code,
                "species_name": row.species_name,
                "level": row.level,
                "version": row.dataset_version,
            }
            for row in sensitivities
        ],
        "datasets": list(DATASETS),
    }


@router.post("/datasets/{dataset}/activate")
def activate_dataset(
    dataset: str,
    data: ActivateIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("researcher", "auditor")),
):
    if dataset not in DATASETS:
        raise HTTPException(404, "未知数据集")
    try:
        revision = activate(db, dataset, data.version)
    except FileNotFoundError as exc:
        raise HTTPException(404, f"样本文件缺失：{exc}") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    from ..spatial_delivery import invalidate_after_dataset_change

    stale = invalidate_after_dataset_change(db, dataset, revision.version)
    db.flush()
    db.commit()
    return {
        "dataset": dataset,
        "active_version": revision.version,
        "fingerprint": revision.fingerprint,
        "stale_publications": [publication.code for publication in stale],
        "note": "受影响的分区与文件已标记为过期，必须重新检查后才能再次生成交付。",
    }


# -------------------------------------------------------------- publications
def _get_publication(db: Session, publication_id: int) -> PublicationRun:
    publication = db.get(PublicationRun, publication_id)
    if publication is None:
        raise HTTPException(404, "发布记录不存在")
    return publication


@router.get("/publications")
def list_publications(
    species_code: str | None = None,
    scope_code: str | None = None,
    season_code: str | None = None,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    query = select(PublicationRun).order_by(PublicationRun.id.desc())
    if species_code:
        query = query.where(PublicationRun.species_code == species_code)
    if scope_code:
        query = query.where(PublicationRun.scope_code == scope_code)
    if season_code:
        query = query.where(PublicationRun.season_code == season_code)
    rows = list(db.execute(query).scalars())
    result = []
    for publication in rows:
        data = publication_out(publication)
        data["published_region_count"] = sum(
            1
            for region in db.execute(
                select(PartitionRegion)
                .where(PartitionRegion.publication_id == publication.id)
                .where(PartitionRegion.status == "published")
            ).scalars()
        )
        data["withheld_region_count"] = sum(
            1
            for region in db.execute(
                select(PartitionRegion)
                .where(PartitionRegion.publication_id == publication.id)
                .where(PartitionRegion.status == "withheld")
            ).scalars()
        )
        result.append(data)
    return result


@router.post("/publications")
def create_publication_endpoint(
    data: PublicationIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("researcher", "auditor")),
):
    run = db.get(SurveyRun, data.run_id)
    if run is None:
        raise HTTPException(404, "调查运行不存在")
    try:
        publication = create_publication(
            db,
            species_code=data.species_code,
            scope_code=data.scope_code,
            season_code=run.season_code,
            run=run,
            actor=user.username,
            note=data.note,
        )
    except (PublicationError, DeliveryError) as exc:
        db.rollback()
        _raise(exc)
    except LookupError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return publication_out(publication)


@router.get("/publications/{publication_id}")
def get_publication(
    publication_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)
):
    publication = _get_publication(db, publication_id)
    regions = list(
        db.execute(
            select(PartitionRegion)
            .where(PartitionRegion.publication_id == publication.id)
            .order_by(PartitionRegion.id)
        ).scalars()
    )
    data = publication_out(publication)
    data["published_region_count"] = sum(1 for region in regions if region.status == "published")
    data["withheld_region_count"] = sum(1 for region in regions if region.status == "withheld")
    return {
        "publication": data,
        "regions": [region_internal_out(db, region) for region in regions],
        "checks": [
            {
                "check_code": row.check_code,
                "status": row.status,
                "detail": row.detail,
                "data": row.data,
            }
            for row in db.execute(
                select(PublicationCheck)
                .where(PublicationCheck.publication_id == publication.id)
                .order_by(PublicationCheck.id)
            ).scalars()
        ],
    }


@router.get("/publications/{publication_id}/reconciliation")
def reconciliation(
    publication_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)
):
    publication = _get_publication(db, publication_id)
    return internal_report(db, publication)


@router.post("/publications/{publication_id}/publish")
def publish_endpoint(
    publication_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("researcher", "auditor")),
):
    publication = _get_publication(db, publication_id)
    try:
        result = publish(db, publication, user.username)
    except DeliveryError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, exc.message) from exc
    db.commit()
    return {
        "publication": publication_out(publication),
        "materials": materials_out(db, publication),
    }


@router.get("/materials")
def list_materials(
    publication_id: int | None = None,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    publication = _get_publication(db, publication_id) if publication_id else None
    return materials_out(db, publication)


@router.post("/materials/{material_code}/withdraw")
def withdraw_material(
    material_code: str,
    data: WithdrawIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("researcher", "auditor")),
):
    material = db.execute(
        select(PublicMaterial).where(PublicMaterial.material_code == material_code)
    ).scalar_one_or_none()
    if material is None:
        raise HTTPException(404, "公开材料不存在")
    if material.status == "withdrawn":
        raise HTTPException(409, "该材料已撤回")
    withdraw(db, material, data.reason, user.username)
    db.commit()
    return materials_out(db, db.get(PublicationRun, material.publication_id))


@router.get("/compare")
def compare_endpoint(
    first: int,
    second: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    first_publication = _get_publication(db, first)
    second_publication = _get_publication(db, second)
    try:
        return compare_publications(db, first_publication, second_publication)
    except DeliveryError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc


# --------------------------------------------------------------------- public
PUBLIC_FORBIDDEN_PARAMS = {
    "lat",
    "lon",
    "lng",
    "latitude",
    "longitude",
    "bbox",
    "extent",
    "precision",
    "resolution",
    "cell",
    "grid",
    "station",
    "station_id",
    "record",
    "record_id",
    "external_id",
    "route",
    "track",
    "geometry",
    "q",
    "query",
    "filter",
    "site",
}


def _reject_fine_params(request: Request) -> None:
    for key in request.query_params.keys():
        if key.lower() in PUBLIC_FORBIDDEN_PARAMS:
            raise HTTPException(
                400,
                f"公开查询不接受细粒度筛选参数 {key}；只能按发布编号查看整块发布分区，"
                "不能用细粒度筛选绕过发布边界。",
            )


def _public_publication(db: Session, code: str) -> PublicationRun:
    publication = db.execute(
        select(PublicationRun).where(PublicationRun.code == code)
    ).scalar_one_or_none()
    if publication is None:
        raise HTTPException(404, "发布记录不存在")
    scope = db.execute(
        select(RecipientScope).where(RecipientScope.code == publication.scope_code)
    ).scalar_one_or_none()
    if scope is None or not scope.external:
        raise HTTPException(404, "该发布不属于对外接收范围")
    if publication.status == "stale":
        raise HTTPException(409, "发布输入已变化，该版本暂停对外提供，需重新检查。")
    if publication.status not in {"published", "superseded"}:
        raise HTTPException(409, "该发布尚未生成对外交付。")
    return publication


@public_router.get("/publications")
def public_publications(request: Request, db: Session = Depends(get_db)):
    _reject_fine_params(request)
    rows = list(
        db.execute(
            select(PublicationRun)
            .where(PublicationRun.status.in_(("published", "superseded")))
            .order_by(PublicationRun.id.desc())
        ).scalars()
    )
    result = []
    for publication in rows:
        scope = db.execute(
            select(RecipientScope).where(RecipientScope.code == publication.scope_code)
        ).scalar_one_or_none()
        if scope is None or not scope.external:
            continue
        result.append(publication_out(publication))
    return result


@public_router.get("/publications/{code}/map")
def public_map(code: str, request: Request, db: Session = Depends(get_db)):
    _reject_fine_params(request)
    publication = _public_publication(db, code)
    try:
        payload = build_public_payload(db, publication)
    except DeliveryError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return {
        "publication": payload["publication"],
        "map": payload["map"],
        "stats": payload["stats"],
    }


@public_router.get("/publications/{code}/stats")
def public_stats(code: str, request: Request, db: Session = Depends(get_db)):
    _reject_fine_params(request)
    publication = _public_publication(db, code)
    return build_public_payload(db, publication)["stats"]


@public_router.get("/publications/{code}/download.csv")
def public_download(code: str, request: Request, db: Session = Depends(get_db)):
    _reject_fine_params(request)
    publication = _public_publication(db, code)
    material_code = f"CSV-{publication.code}"
    try:
        material = active_material(db, material_code)
    except DeliveryError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return Response(
        content=material.csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{publication.code}.csv"',
            "X-Disclosure-Level": publication.sensitivity_level,
        },
    )


@public_router.get("/materials/{material_code}")
def public_material(material_code: str, request: Request, db: Session = Depends(get_db)):
    _reject_fine_params(request)
    try:
        material = active_material(db, material_code)
    except DeliveryError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    publication = db.get(PublicationRun, material.publication_id)
    if publication is None:
        raise HTTPException(404, "发布记录不存在")
    scope = db.execute(
        select(RecipientScope).where(RecipientScope.code == publication.scope_code)
    ).scalar_one_or_none()
    if scope is None or not scope.external:
        raise HTTPException(404, "该材料不属于对外接收范围")
    if material.status != "active":
        raise HTTPException(410, "该材料已不再提供；撤回不代表旧信息已被消除。")
    if material.kind == "download":
        return Response(
            content=material.csv_text,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{material.material_code}.csv"'},
        )
    return {
        "material_code": material.material_code,
        "publication_code": publication.code,
        "kind": material.kind,
        "sensitivity_level": material.sensitivity_level,
        "content_hash": material.content_hash,
        "regions": [
            {
                "region_code": region.get("region_code"),
                "extent_key": region.get("extent_key"),
                "display_cell_m": region.get("display_cell_m"),
            }
            for region in (material.regions or [])
        ],
        "payload": material.payload,
    }
