"""Delivery generator for spatial survey data.

The generator deliberately reads only publication rows whose checks passed and
whose partition/statistics rows were written in the same check cycle. It never
consults raw coordinates, tracks or station positions, so a public response can
not leak a finer granularity than the disclosure partition allows.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    DatasetRevision,
    PartitionRegion,
    PublicMaterial,
    PublicationCheck,
    PublicationRun,
    RegionCell,
    RegionEffort,
    RegionObservation,
    RegionRoute,
    SpeciesSensitivity,
    SpeciesSuitability,
    RecipientScope,
    utcnow,
)
from .spatial_datasets import SENSITIVITY_LABELS, active_revision


class DeliveryError(Exception):
    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _checked_publication(db: Session, publication: PublicationRun) -> PartitionRegion:
    if publication.status not in {"checked", "published"} or not publication.checks_passed:
        raise DeliveryError(
            f"发布 {publication.code} 尚未通过检查（当前状态 {publication.status}），不能生成交付。",
            409,
        )
    regions = list(
        db.execute(
            select(PartitionRegion)
            .where(PartitionRegion.publication_id == publication.id)
            .order_by(PartitionRegion.id)
        ).scalars()
    )
    if any(region.base_cell_count <= 0 for region in regions):
        raise DeliveryError("分区记录不完整，不能生成交付。", 409)
    return regions


def _species_name(db: Session, species_code: str, sensitivity_version: int) -> str:
    row = db.execute(
        select(SpeciesSensitivity)
        .where(SpeciesSensitivity.species_code == species_code)
        .where(SpeciesSensitivity.dataset_version == sensitivity_version)
    ).scalar_one_or_none()
    return row.species_name if row else species_code


def _scope(db: Session, code: str) -> RecipientScope | None:
    return db.execute(select(RecipientScope).where(RecipientScope.code == code)).scalar_one_or_none()


def _public_region_properties(region: PartitionRegion) -> dict[str, Any]:
    properties = {
        "region_code": region.region_code,
        "extent_key": region.extent_key,
        "display_cell_m": round(region.display_cell_m, 1),
        "display_area_ha": round(region.display_area_m2 / 10_000, 2),
        "candidate_habitat_ha": round(region.candidate_habitat_m2 / 10_000, 2),
        "observation_count": region.observation_count,
        "present_count": region.present_count,
        "effective_hours": round(region.effective_hours, 2),
        "route_length_km": round(region.route_length_m / 1000, 2),
        "effort_known": region.effort_known,
        "occurrence_rate": region.occurrence_rate,
        "sensitivity_level": region.sensitivity_level,
        "sensitivity_label": SENSITIVITY_LABELS.get(region.sensitivity_level, region.sensitivity_level),
        "sensitivity_floor": region.threshold_snapshot.get("level_baseline", {})
        if isinstance(region.threshold_snapshot, dict)
        else {},
        "min_candidate_habitat_m2": region.threshold_snapshot.get("min_candidate_habitat_m2")
        if isinstance(region.threshold_snapshot, dict)
        else None,
        "min_region_width_m": region.threshold_snapshot.get("min_region_width_m")
        if isinstance(region.threshold_snapshot, dict)
        else None,
        "withheld": region.status != "published",
    }
    return properties


def _download_rows(regions: list[PartitionRegion]) -> list[dict[str, Any]]:
    rows = []
    for region in regions:
        if region.status != "published":
            continue
        rows.append(
            {
                "region_code": region.region_code,
                "extent_key": region.extent_key,
                "season_code": None,
                "display_cell_m": round(region.display_cell_m, 1),
                "display_area_ha": round(region.display_area_m2 / 10_000, 2),
                "candidate_habitat_ha": round(region.candidate_habitat_m2 / 10_000, 2),
                "observation_count": region.observation_count,
                "present_count": region.present_count,
                "effective_hours": round(region.effective_hours, 2),
                "route_length_km": round(region.route_length_m / 1000, 2),
                "effort_known": region.effort_known,
                "occurrence_rate": region.occurrence_rate,
            }
        )
    return rows


def _csv_bytes(rows: list[dict[str, Any]], publication: PublicationRun) -> str:
    buffer = io.StringIO()
    fieldnames = [
        "season_code",
        "region_code",
        "extent_key",
        "display_cell_m",
        "display_area_ha",
        "candidate_habitat_ha",
        "observation_count",
        "present_count",
        "effective_hours",
        "route_length_km",
        "effort_known",
        "occurrence_rate",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        row = dict(row)
        row["season_code"] = publication.season_code
        writer.writerow(row)
    return buffer.getvalue()


def build_public_payload(
    db: Session,
    publication: PublicationRun,
    *,
    regions: list[PartitionRegion] | None = None,
    include_withheld_geometries: bool = False,
) -> dict[str, Any]:
    """Build the map + statistics payload for one checked publication."""
    regions = regions if regions is not None else _checked_publication(db, publication)
    scope = _scope(db, publication.scope_code)
    species_name = _species_name(db, publication.species_code, publication.sensitivity_version)
    map_features = []
    withheld_features = []
    for region in regions:
        feature = {
            "type": "Feature",
            "geometry": region.display_geometry,
            "properties": _public_region_properties(region),
        }
        if region.status == "published":
            map_features.append(feature)
        else:
            # Withheld regions are count-only, never geometry, in public payloads.
            withheld_features.append(
                {
                    "region_code": region.region_code,
                    "reason_codes": [reason.get("code") for reason in region.reasons or []],
                    "candidate_habitat_ha": round(region.candidate_habitat_m2 / 10_000, 2),
                }
            )
    rows = _download_rows(regions)
    stats_payload = {
        "publication": publication.code,
        "season_code": publication.season_code,
        "species_code": publication.species_code,
        "species_name": species_name,
        "scope_code": publication.scope_code,
        "scope_name": scope.name if scope else publication.scope_code,
        "units": {
            "area": "ha",
            "effective_hours": "h",
            "route_length": "km",
            "occurrence_rate": "present observations / attributed observations",
        },
        "regions": [
            {
                "region_code": row["region_code"],
                "extent_key": row["extent_key"],
                "display_cell_m": row["display_cell_m"],
                "display_area_ha": row["display_area_ha"],
                "candidate_habitat_ha": row["candidate_habitat_ha"],
                "observation_count": row["observation_count"],
                "present_count": row["present_count"],
                "effective_hours": row["effective_hours"],
                "route_length_km": row["route_length_km"],
                "effort_known": row["effort_known"],
                "occurrence_rate": row["occurrence_rate"],
            }
            for row in rows
        ],
        "withheld": withheld_features,
    }
    return {
        "publication": {
            "code": publication.code,
            "species_code": publication.species_code,
            "species_name": species_name,
            "season_code": publication.season_code,
            "scope_code": publication.scope_code,
            "scope_name": scope.name if scope else publication.scope_code,
            "sensitivity_level": publication.sensitivity_level,
            "sensitivity_label": SENSITIVITY_LABELS.get(
                publication.sensitivity_level, publication.sensitivity_level
            ),
            "sensitivity_version": publication.sensitivity_version,
            "habitat_version": publication.habitat_version,
            "suitability_version": publication.suitability_version,
            "published_region_count": sum(
                1 for region in regions if region.status == "published"
            ),
            "withheld_region_count": sum(1 for region in regions if region.status != "published"),
            "unattributed_observations": publication.unattributed_observations,
            "status": publication.status,
        },
        "map": {
            "type": "FeatureCollection",
            "features": map_features,
            "disclosure": (
                "地图仅显示合并后的发布分区；区域边界由公开生境与适生条件计算，"
                "不代表巢点位置，也不允许细粒度筛选绕过发布边界。"
            ),
        },
        "stats": stats_payload,
        "csv": _csv_bytes(rows, publication),
    }


def publish(db: Session, publication: PublicationRun, actor: str, note: str = "") -> dict[str, Any]:
    """Generate map + download material from a checked publication and ledger them."""
    if publication.status in {"stale", "stopped", "superseded"}:
        raise DeliveryError(
            f"发布 {publication.code} 状态为 {publication.status}，"
            "应先重新检查受影响分区，不能混用旧分区与新统计。",
            409,
        )
    if publication.status == "published" and publication.published_at:
        return {"publication": publication, "materials": _materials_for(db, publication)}
    if publication.status != "checked":
        raise DeliveryError(
            f"发布 {publication.code} 状态为 {publication.status}，只有通过检查的分区可以生成交付。",
            409,
        )
    payload = build_public_payload(db, publication)
    if not payload["map"]["features"]:
        raise DeliveryError("该发布没有任何可公开分区，拒绝生成空地图。", 409)

    # A new delivery for the same (species, scope, season) supersedes older ones,
    # but their ledger rows stay readable: withdrawal is not erasure.
    previous = list(
        db.execute(
            select(PublicationRun)
            .where(PublicationRun.species_code == publication.species_code)
            .where(PublicationRun.scope_code == publication.scope_code)
            .where(PublicationRun.season_code == publication.season_code)
            .where(PublicationRun.id != publication.id)
            .where(PublicationRun.status.in_(("published", "checked", "stale", "stopped")))
            .order_by(PublicationRun.id)
        ).scalars()
    )
    for old in previous:
        if old.status == "published":
            old.status = "superseded"
            old.superseded_by_id = publication.id
            old.stale_reason = f"由 {publication.code} 取代"
            for material in _materials_for(db, old):
                if material.status == "active":
                    material.status = "superseded"
                    material.superseded_by_code = None
            db.flush()

    regions = list(
        db.execute(
            select(PartitionRegion)
            .where(PartitionRegion.publication_id == publication.id)
            .where(PartitionRegion.status == "published")
            .order_by(PartitionRegion.id)
        ).scalars()
    )
    region_ranges = [
        {
            "region_code": region.region_code,
            "extent_key": region.extent_key,
            "geometry": region.display_geometry,
            # Retained base units travel with the material so a later season can
            # prove the old range is still covered (withdrawal is not erasure).
            "base_cells": [cell for cell in (region.candidate_cells or [])],
            "display_cell_m": region.display_cell_m,
            "candidate_habitat_m2": region.candidate_habitat_m2,
        }
        for region in regions
    ]
    map_code = f"MAP-{publication.code}"
    csv_code = f"CSV-{publication.code}"
    map_body = json.dumps(payload["map"], sort_keys=True, ensure_ascii=False).encode("utf-8")
    map_row = PublicMaterial(
        publication_id=publication.id,
        scope_code=publication.scope_code,
        species_code=publication.species_code,
        kind="map",
        material_code=map_code,
        content_hash=hashlib.sha256(map_body).hexdigest(),
        sensitivity_level=publication.sensitivity_level,
        thresholds=publication.thresholds,
        regions=region_ranges,
        status="active",
        payload=payload["map"],
        csv_text="",
        note=note or "发布地图",
    )
    csv_row = PublicMaterial(
        publication_id=publication.id,
        scope_code=publication.scope_code,
        species_code=publication.species_code,
        kind="download",
        material_code=csv_code,
        content_hash=hashlib.sha256(payload["csv"].encode("utf-8")).hexdigest(),
        sensitivity_level=publication.sensitivity_level,
        thresholds=publication.thresholds,
        regions=region_ranges,
        status="active",
        payload=payload["stats"],
        csv_text=payload["csv"],
        note=note or "调查数据下载",
    )
    db.add_all([map_row, csv_row])
    publication.status = "published"
    publication.published_at = utcnow()
    db.flush()
    return {"publication": publication, "materials": [map_row, csv_row]}


def _materials_for(db: Session, publication: PublicationRun) -> list[PublicMaterial]:
    return list(
        db.execute(
            select(PublicMaterial)
            .where(PublicMaterial.publication_id == publication.id)
            .order_by(PublicMaterial.id)
        ).scalars()
    )


def active_material(db: Session, material_code: str) -> PublicMaterial:
    material = db.execute(
        select(PublicMaterial).where(PublicMaterial.material_code == material_code)
    ).scalar_one_or_none()
    if material is None:
        raise DeliveryError("公开材料不存在", 404)
    if material.status == "withdrawn":
        raise DeliveryError("该材料已撤回，不再提供；撤回不表示旧信息已被消除。", 410)
    if material.status in {"stale", "superseded"}:
        raise DeliveryError(
            f"该材料状态为 {material.status}，发布输入已变化，需重新检查并生成新版本。", 409
        )
    return material


def withdraw(db: Session, material: PublicMaterial, reason: str, actor: str) -> PublicMaterial:
    material.status = "withdrawn"
    material.withdrawn_at = utcnow()
    material.note = (material.note + " | " if material.note else "") + f"撤回：{reason}（撤回链接不代表旧信息已消除）"
    # The range stays in the ledger; future publications must still account for it.
    material.regions = list(material.regions or [])
    db.flush()
    return material


def invalidate_after_dataset_change(db: Session, dataset: str, new_version: int) -> list[PublicationRun]:
    """Mark publications and materials that used an older dataset version as stale.

    A stale publication must be re-checked; its delivered files stop being served,
    because mixing an old partition with new statistics is never allowed.
    """
    stale: list[PublicationRun] = []
    field = {
        "habitat": PublicationRun.habitat_version,
        "suitability": PublicationRun.suitability_version,
        "sensitivity": PublicationRun.sensitivity_version,
        "scopes": PublicationRun.scope_version,
    }.get(dataset)
    if field is None:
        return stale
    rows = list(
        db.execute(
            select(PublicationRun).where(PublicationRun.status.in_(("checked", "published", "stale")))
        ).scalars()
    )
    for publication in rows:
        if getattr(publication, field.key) >= new_version:
            continue
        if publication.status != "stale":
            stale.append(publication)
            publication.status = "stale"
            publication.stale_reason = (
                f"{dataset} 数据已更新至 v{new_version}；受影响分区与文件必须重新检查，"
                "不得混用旧分区与新统计。"
            )
            for material in _materials_for(db, publication):
                if material.status == "active":
                    material.status = "stale"
                    material.note = (
                        material.note + " | " if material.note else ""
                    ) + publication.stale_reason
    db.flush()
    return stale


def publication_out(publication: PublicationRun) -> dict[str, Any]:
    return {
        "id": publication.id,
        "code": publication.code,
        "species_code": publication.species_code,
        "scope_code": publication.scope_code,
        "season_code": publication.season_code,
        "status": publication.status,
        "survey_run_id": publication.survey_run_id,
        "sensitivity_level": publication.sensitivity_level,
        "sensitivity_version": publication.sensitivity_version,
        "habitat_version": publication.habitat_version,
        "suitability_version": publication.suitability_version,
        "scope_version": publication.scope_version,
        "fingerprint": publication.fingerprint,
        "thresholds": publication.thresholds,
        "region_count": publication.region_count,
        "published_region_count": publication.published_region_count,
        "unattributed_observations": publication.unattributed_observations,
        "checks_passed": publication.checks_passed,
        "stop_reason": publication.stop_reason,
        "stale_reason": publication.stale_reason,
        "superseded_by_id": publication.superseded_by_id,
        "created_by": publication.created_by,
        "note": publication.note,
        "created_at": publication.created_at.isoformat() if publication.created_at else None,
        "checked_at": publication.checked_at.isoformat() if publication.checked_at else None,
        "published_at": publication.published_at.isoformat() if publication.published_at else None,
    }


def region_internal_out(db: Session, region: PartitionRegion, *, include_cells: bool = True) -> dict[str, Any]:
    cells = []
    if include_cells:
        for row in db.execute(
            select(RegionCell)
            .where(RegionCell.region_id == region.id)
            .order_by(RegionCell.cell_code)
        ).scalars():
            cells.append(
                {
                    "cell_code": row.cell_code,
                    "candidate_habitat_m2": row.candidate_habitat_m2,
                    "observation_count": row.observation_count,
                    "present_count": row.present_count,
                }
            )
    effort = [
        {
            "window_start": row.window_start.isoformat(),
            "window_end": row.window_end.isoformat(),
            "hours": row.hours,
            "merged_intervals": row.merged_intervals,
            "unit_ids": row.unit_ids,
            "disposition": row.disposition,
        }
        for row in db.execute(
            select(RegionEffort)
            .where(RegionEffort.region_id == region.id)
            .order_by(RegionEffort.id)
        ).scalars()
    ]
    return {
        "id": region.id,
        "region_code": region.region_code,
        "extent_key": region.extent_key,
        "component_key": region.component_key,
        "family": region.component_key,
        "status": region.status,
        "geometry": region.geometry,
        "display_geometry": region.display_geometry,
        "display_cell_m": region.display_cell_m,
        "area_m2": region.area_m2,
        "display_area_m2": region.display_area_m2,
        "candidate_habitat_m2": region.candidate_habitat_m2,
        "width_m": region.width_m,
        "base_cell_count": region.base_cell_count,
        "candidate_cells": region.candidate_cells,
        "base_cells": cells,
        "effort_known": region.effort_known,
        "effort_units": region.effort_units,
        "merged_effort_windows": region.merged_effort_windows,
        "reasons": region.reasons,
        "sensitivity_level": region.sensitivity_level,
        "threshold_snapshot": region.threshold_snapshot,
        "observation_count": region.observation_count,
        "present_count": region.present_count,
        "duplicate_materials": region.duplicate_materials,
        "effective_hours": region.effective_hours,
        "route_length_m": region.route_length_m,
        "effort_unknown_units": region.effort_unknown_units,
        "occurrence_rate": region.occurrence_rate,
        "effort_blocks": effort,
    }


def internal_report(db: Session, publication: PublicationRun) -> dict[str, Any]:
    """Internal reconciliation. Never exposed on public routes."""
    regions = list(
        db.execute(
            select(PartitionRegion)
            .where(PartitionRegion.publication_id == publication.id)
            .order_by(PartitionRegion.id)
        ).scalars()
    )
    checks = [
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
    ]
    observations = [
        {
            "region_code": next(
                (r.region_code for r in regions if r.id == row.region_id), None
            ),
            "record_id": row.record_id,
            "external_id": row.external_id,
            "material_key": row.material_key,
            "source": row.source,
            "verdict": row.verdict,
            "confidence": row.confidence,
            "disposition": row.disposition,
            "reason": row.reason,
        }
        for row in db.execute(
            select(RegionObservation)
            .where(RegionObservation.publication_id == publication.id)
            .order_by(RegionObservation.id)
        ).scalars()
    ]
    efforts = [
        {
            "region_code": next(
                (r.region_code for r in regions if r.id == row.region_id), None
            ),
            "window_start": row.window_start.isoformat(),
            "window_end": row.window_end.isoformat(),
            "hours": row.hours,
            "merged_intervals": row.merged_intervals,
            "unit_ids": row.unit_ids,
            "disposition": row.disposition,
            "reason": row.reason,
        }
        for row in db.execute(
            select(RegionEffort)
            .where(RegionEffort.publication_id == publication.id)
            .order_by(RegionEffort.id)
        ).scalars()
    ]
    routes = [
        {
            "region_code": next(
                (r.region_code for r in regions if r.id == row.region_id), None
            ),
            "record_id": row.record_id,
            "length_m": row.length_m,
            "disposition": row.disposition,
            "reason": row.reason,
        }
        for row in db.execute(
            select(RegionRoute)
            .where(RegionRoute.publication_id == publication.id)
            .order_by(RegionRoute.id)
        ).scalars()
    ]
    from .spatial import cell_code  # local import to avoid a cycle at module load

    return {
        "publication": publication_out(publication),
        "regions": [region_internal_out(db, region) for region in regions],
        "checks": checks,
        "reconciliation": {
            "observation_rows": observations,
            "observation_totals": {
                "included": sum(1 for row in observations if row["disposition"] == "included"),
                "duplicates": sum(1 for row in observations if row["disposition"] == "duplicate"),
                "unattributed": sum(
                    1 for row in observations if row["disposition"] == "unattributed"
                ),
            },
            "effort_rows": efforts,
            "effort_totals": {
                "published_hours": round(
                    sum(r.effective_hours for r in regions if r.status == "published"), 2
                ),
                "merged_hours": round(
                    sum(row["hours"] for row in efforts if row["disposition"] == "included"), 2
                ),
                "unattributed_units": sum(
                    1 for row in efforts if row["disposition"] != "included"
                ),
            },
            "route_rows": routes,
            "route_totals": {
                "published_km": round(
                    sum(r.route_length_m for r in regions if r.status == "published") / 1000, 2
                ),
                "duplicates": sum(1 for row in routes if row["disposition"] == "duplicate"),
            },
        },
        "base_cell_code_example": cell_code(0, 0),
    }


def materials_out(db: Session, publication: PublicationRun | None = None) -> list[dict[str, Any]]:
    query = select(PublicMaterial).order_by(PublicMaterial.id)
    if publication is not None:
        query = query.where(PublicMaterial.publication_id == publication.id)
    rows = list(db.execute(query).scalars())
    codes = {r.id: r.code for r in db.execute(select(PublicationRun)).scalars()}
    return [
        {
            "material_code": row.material_code,
            "publication_code": codes.get(row.publication_id),
            "kind": row.kind,
            "scope_code": row.scope_code,
            "species_code": row.species_code,
            "status": row.status,
            "sensitivity_level": row.sensitivity_level,
            "content_hash": row.content_hash,
            "regions": [
                {
                    "region_code": region.get("region_code"),
                    "extent_key": region.get("extent_key"),
                    "display_cell_m": region.get("display_cell_m"),
                }
                for region in (row.regions or [])
            ],
            "superseded_by_code": row.superseded_by_code,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "withdrawn_at": row.withdrawn_at.isoformat() if row.withdrawn_at else None,
            "note": row.note,
        }
        for row in rows
    ]


def compare_publications(
    db: Session, first: PublicationRun, second: PublicationRun
) -> dict[str, Any]:
    """Compare two published seasons on their shared region extents only."""
    for publication in (first, second):
        if publication.status not in {"published", "superseded", "stale"}:
            raise DeliveryError(
                f"发布 {publication.code} 状态为 {publication.status}，不能用于比较。", 409
            )
    rows = {}
    for publication in (first, second):
        regions = list(
            db.execute(
                select(PartitionRegion)
                .where(PartitionRegion.publication_id == publication.id)
                .where(PartitionRegion.status == "published")
            ).scalars()
        )
        rows[publication.id] = {region.extent_key: region for region in regions}
    shared = sorted(set(rows[first.id]) & set(rows[second.id]))
    only_first = sorted(set(rows[first.id]) - set(rows[second.id]))
    only_second = sorted(set(rows[second.id]) - set(rows[first.id]))
    comparable = []
    for key in shared:
        a = rows[first.id][key]
        b = rows[second.id][key]
        comparable.append(
            {
                "extent_key": key,
                "region_code_first": a.region_code,
                "region_code_second": b.region_code,
                "candidate_habitat_ha": [round(a.candidate_habitat_m2 / 1e4, 2), round(b.candidate_habitat_m2 / 1e4, 2)],
                "observation_count": [a.observation_count, b.observation_count],
                "present_count": [a.present_count, b.present_count],
                "effective_hours": [round(a.effective_hours, 2), round(b.effective_hours, 2)],
                "route_length_km": [round(a.route_length_m / 1e3, 2), round(b.route_length_m / 1e3, 2)],
                "occurrence_rate": [a.occurrence_rate, b.occurrence_rate],
                "delta_observations": b.observation_count - a.observation_count,
                "comparable": True,
            }
        )

    def only_rows(keys, source):
        return [
            {
                "extent_key": key,
                "region_code": source[key].region_code,
                "candidate_habitat_ha": round(source[key].candidate_habitat_m2 / 1e4, 2),
                "observation_count": source[key].observation_count,
                "effective_hours": round(source[key].effective_hours, 2),
                "comparable": False,
                "note": "缺少共同范围，单独展示；不与另一期直接计算涨跌。",
            }
            for key in keys
        ]

    return {
        "first": {
            "code": first.code,
            "season_code": first.season_code,
            "sensitivity_level": first.sensitivity_level,
            "display_cell_m": (
                list(rows[first.id].values())[0].display_cell_m if rows[first.id] else None
            ),
        },
        "second": {
            "code": second.code,
            "season_code": second.season_code,
            "sensitivity_level": second.sensitivity_level,
            "display_cell_m": (
                list(rows[second.id].values())[0].display_cell_m if rows[second.id] else None
            ),
        },
        "shared_extent_count": len(shared),
        "comparable_rows": comparable,
        "only_first": only_rows(only_first, rows[first.id]),
        "only_second": only_rows(only_second, rows[second.id]),
        "rule": (
            "仅对共同范围（相同 extent_key 的公开分区）计算涨跌；"
            "无法建立共同范围时分开列示，不把两个面积不同的区域直接计算涨跌。"
        ),
    }
