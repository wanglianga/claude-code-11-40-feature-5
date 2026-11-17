"""Sample-file datasets for spatial publication.

All spatial disclosure inputs are supplied as sample files under ``sample_data/``:
public habitat boundaries, nesting-suitability conditions, species sensitivity
levels and recipient scopes. Nothing here reads a real nest point or an external
map platform.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    DatasetRevision,
    HabitatPolygon,
    RecipientScope,
    SpeciesSensitivity,
    SpeciesSuitability,
)
import math

from .utils import _LAT_M, _LON_M_PER_DEG, canonical_hash, to_local  # noqa: F401
from shapely.geometry import shape

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_data"

DATASETS = ("habitat", "suitability", "sensitivity", "scopes")

# A fixed base lattice keeps seasons comparable. Regions may only be coarsened
# from this lattice, never from raw coordinates.
GRID_ORIGIN = (31.060000, 103.630000)  # lat, lon (sample-file origin)
BASE_CELL_M = 100.0
GRID_VERSION = 1

SENSITIVITY_BASELINE: dict[str, dict[str, float]] = {
    # Floor imposed by the species' sensitivity level, independent of recipient.
    "low": {"min_cell_size_m": 100, "min_candidate_habitat_m2": 0, "min_region_width_m": 0},
    "medium": {"min_cell_size_m": 150, "min_candidate_habitat_m2": 20_000, "min_region_width_m": 150},
    "high": {"min_cell_size_m": 200, "min_candidate_habitat_m2": 60_000, "min_region_width_m": 200},
    "critical": {"min_cell_size_m": 500, "min_candidate_habitat_m2": 100_000, "min_region_width_m": 600},
}

SENSITIVITY_LABELS = {
    "low": "低",
    "medium": "中",
    "high": "高",
    "critical": "极高",
}

SCOPE_THRESHOLD_DEFAULTS = {
    "min_cell_size_m": 0.0,
    "min_candidate_habitat_m2": 0.0,
    "min_region_width_m": 0.0,
    "allow_narrow_habitat": True,
    "merge_adjacent": True,
}


def _file_bytes(dataset: str, version: int) -> tuple[bytes, str]:
    path = SAMPLE_DIR / f"{dataset}_v{version}.json"
    raw = path.read_bytes()
    return raw, path.name


def load_dataset(db: Session, dataset: str, version: int) -> DatasetRevision:
    """Load one sample-file dataset version. Idempotent by (dataset, version)."""
    if dataset not in DATASETS:
        raise ValueError(f"未知数据集 {dataset}")
    raw, name = _file_bytes(dataset, version)
    data = json.loads(raw.decode("utf-8"))
    fingerprint = canonical_hash(data)
    revision = db.execute(
        select(DatasetRevision)
        .where(DatasetRevision.dataset == dataset)
        .where(DatasetRevision.version == version)
    ).scalar_one_or_none()
    if revision is None:
        revision = DatasetRevision(
            dataset=dataset,
            version=version,
            fingerprint=fingerprint,
            source_file=str(SAMPLE_DIR / name),
            notes=data.get("notes", ""),
            active=True,
        )
        db.add(revision)
    else:
        revision.fingerprint = fingerprint
        revision.notes = data.get("notes", "")
    db.flush()

    if dataset == "habitat":
        _load_habitat(db, revision, data)
    elif dataset == "suitability":
        _load_suitability(db, revision, data)
    elif dataset == "sensitivity":
        _load_sensitivity(db, revision, data)
    elif dataset == "scopes":
        _load_scopes(db, revision, data)
    db.flush()
    return revision


def _load_habitat(db: Session, revision: DatasetRevision, data: dict[str, Any]) -> None:
    for row in db.execute(
        select(HabitatPolygon).where(HabitatPolygon.dataset_version == revision.version)
    ).scalars():
        db.delete(row)
    db.flush()
    origin_lat = float(data.get("origin", {}).get("lat", GRID_ORIGIN[0]))
    origin_lon = float(data.get("origin", {}).get("lon", GRID_ORIGIN[1]))
    for feature in data.get("features", []):
        geom = shape(feature["geometry"])
        local = to_local(geom, origin_lat, origin_lon)
        db.add(
            HabitatPolygon(
                dataset_version=revision.version,
                code=feature["code"],
                name=feature["name"],
                habitat_class=feature["habitat_class"],
                geometry=feature["geometry"],
                area_m2=round(float(local.area), 2),
                elevation_min_m=float(feature["elevation_min_m"]),
                elevation_max_m=float(feature["elevation_max_m"]),
                connectivity_group=feature.get("connectivity_group", ""),
                active=True,
            )
        )


def _load_suitability(db: Session, revision: DatasetRevision, data: dict[str, Any]) -> None:
    for row in db.execute(
        select(SpeciesSuitability).where(SpeciesSuitability.dataset_version == revision.version)
    ).scalars():
        db.delete(row)
    db.flush()
    for item in data.get("species", []):
        db.add(
            SpeciesSuitability(
                species_code=item["species_code"],
                dataset_version=revision.version,
                habitat_classes=list(item["habitat_classes"]),
                elevation_min_m=float(item["elevation_min_m"]),
                elevation_max_m=float(item["elevation_max_m"]),
                notes=item.get("notes", ""),
            )
        )


def _load_sensitivity(db: Session, revision: DatasetRevision, data: dict[str, Any]) -> None:
    for row in db.execute(
        select(SpeciesSensitivity).where(SpeciesSensitivity.dataset_version == revision.version)
    ).scalars():
        db.delete(row)
    db.flush()
    for item in data.get("species", []):
        db.add(
            SpeciesSensitivity(
                species_code=item["species_code"],
                species_name=item.get("name", item["species_code"]),
                level=item["level"],
                dataset_version=revision.version,
                notes=item.get("notes", ""),
            )
        )


def _load_scopes(db: Session, revision: DatasetRevision, data: dict[str, Any]) -> None:
    for item in data.get("scopes", []):
        scope = db.execute(
            select(RecipientScope).where(RecipientScope.code == item["code"])
        ).scalar_one_or_none()
        thresholds = dict(SCOPE_THRESHOLD_DEFAULTS)
        thresholds.update(item.get("thresholds", {}))
        if scope is None:
            scope = RecipientScope(
                code=item["code"],
                name=item["name"],
                audience=item["audience"],
                purpose=item["purpose"],
                external=bool(item.get("external", True)),
                allowed_levels=list(item["allowed_levels"]),
                thresholds=thresholds,
                dataset_version=revision.version,
                notes=item.get("notes", ""),
            )
            db.add(scope)
        else:
            scope.name = item["name"]
            scope.audience = item["audience"]
            scope.purpose = item["purpose"]
            scope.external = bool(item.get("external", True))
            scope.allowed_levels = list(item["allowed_levels"])
            scope.thresholds = thresholds
            scope.dataset_version = revision.version
            scope.notes = item.get("notes", "")


def activate(db: Session, dataset: str, version: int) -> DatasetRevision:
    revision = load_dataset(db, dataset, version)
    for other in db.execute(
        select(DatasetRevision).where(DatasetRevision.dataset == dataset)
    ).scalars():
        other.active = other.id == revision.id
    db.flush()
    return revision


def active_revision(db: Session, dataset: str) -> DatasetRevision:
    revision = (
        db.execute(
            select(DatasetRevision)
            .where(DatasetRevision.dataset == dataset)
            .where(DatasetRevision.active.is_(True))
            .order_by(DatasetRevision.version.desc())
        )
        .scalars()
        .first()
    )
    if revision is None:
        raise LookupError(f"数据集 {dataset} 尚未激活")
    return revision


def seed_default_datasets(db: Session) -> None:
    existing = db.execute(select(DatasetRevision)).scalars().first()
    if existing is not None:
        return
    for dataset in DATASETS:
        load_dataset(db, dataset, 1)
        revision = db.execute(
            select(DatasetRevision)
            .where(DatasetRevision.dataset == dataset)
            .where(DatasetRevision.version == 1)
        ).scalar_one()
        for other in db.execute(
            select(DatasetRevision).where(DatasetRevision.dataset == dataset)
        ).scalars():
            other.active = other.id == revision.id
    db.flush()


def effective_thresholds(scope: RecipientScope, level: str) -> dict[str, Any]:
    base = SENSITIVITY_BASELINE.get(level, SENSITIVITY_BASELINE["low"])
    disclosed = dict(SCOPE_THRESHOLD_DEFAULTS)
    disclosed.update(scope.thresholds or {})
    return {
        "min_cell_size_m": max(float(disclosed["min_cell_size_m"]), float(base["min_cell_size_m"])),
        "min_candidate_habitat_m2": max(
            float(disclosed["min_candidate_habitat_m2"]), float(base["min_candidate_habitat_m2"])
        ),
        "min_region_width_m": max(
            float(disclosed["min_region_width_m"]), float(base["min_region_width_m"])
        ),
        "allow_narrow_habitat": bool(disclosed["allow_narrow_habitat"]),
        "merge_adjacent": bool(disclosed["merge_adjacent"]),
        "level_baseline": base,
        "scope_thresholds": disclosed,
    }


def lonlat_to_local(lat: float, lon: float) -> tuple[float, float]:
    """Project a lon/lat point into the fixed local metre frame."""
    lon_scale = _LON_M_PER_DEG * math.cos(math.radians(GRID_ORIGIN[0]))
    return (lon - GRID_ORIGIN[1]) * lon_scale, (lat - GRID_ORIGIN[0]) * _LAT_M


def local_to_lonlat(x: float, y: float) -> tuple[float, float]:
    lon_scale = _LON_M_PER_DEG * math.cos(math.radians(GRID_ORIGIN[0]))
    return GRID_ORIGIN[0] + y / _LAT_M, GRID_ORIGIN[1] + x / lon_scale
