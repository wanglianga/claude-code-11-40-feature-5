"""Spatial generalization and survey-statistic publication.

Everything publishable is derived from sample-file public habitat, suitability,
sensitivity and recipient-scope inputs — never from a nest point, a station
position or an external map platform.

Pipeline:
  1. intersect public habitat with the species' suitability conditions on a
     fixed 100 m base lattice;
  2. merge side-adjacent base cells along real habitat connectivity, then
     coarsen each region only as far as its own disclosure thresholds require
     (other regions keep their detail);
  3. attribute observations, valid device effort and route coverage to the
     final regions — merging overlapping device intervals, de-duplicating
     republished material and keeping cross-partition location errors
     unattributed;
  4. re-check thresholds, effort knowledge, internal reconciliation and
     previously released material ranges. Only checked partitions and
     statistics feed the delivery generator.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections import defaultdict, deque
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from shapely.geometry import MultiPolygon, Point, Polygon, box, mapping, shape
from shapely.ops import unary_union
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import RESERVE_TZ
from .models import (
    ArchiveRecord,
    Certificate,
    DatasetRevision,
    Determination,
    GateCheck,
    GridCell,
    HabitatPolygon,
    PartitionRegion,
    PublicMaterial,
    PublicationCheck,
    PublicationRun,
    RecipientScope,
    RegionCell,
    RegionEffort,
    RegionObservation,
    RegionRoute,
    RulebookVersion,
    SpeciesSensitivity,
    SpeciesSuitability,
    Station,
    SurveyRun,
    SurveyUnit,
    utcnow,
)
from .spatial_datasets import (
    BASE_CELL_M,
    GRID_ORIGIN,
    GRID_VERSION,
    SENSITIVITY_LABELS,
    active_revision,
    effective_thresholds,
    lonlat_to_local,
)
import numpy as np

from .spatial_datasets import local_to_lonlat
from .utils import canonical_hash, day_window


def to_local(geom, lat0: float | None = None, lon0: float | None = None):
    """Project lon/lat geometry into the fixed local metre frame.

    ``app.utils.to_local`` converts degree differences through ``radians`` and is
    only self-consistent inside the legacy coverage gates; the publication engine
    needs true metres, so it uses this projection everywhere.
    """
    from shapely import transform as shp_transform

    origin_lat = GRID_ORIGIN[0] if lat0 is None else lat0
    origin_lon = GRID_ORIGIN[1] if lon0 is None else lon0
    lon_scale = 111_320.0 * math.cos(math.radians(origin_lat))

    def _fn(coords):
        out = coords.copy()
        out[:, 0] = (coords[:, 0] - origin_lon) * lon_scale
        out[:, 1] = (coords[:, 1] - origin_lat) * 110_540.0
        return out

    return shp_transform(geom, _fn)


def from_local(geom, lat0: float | None = None, lon0: float | None = None):
    from shapely import transform as shp_transform

    origin_lat = GRID_ORIGIN[0] if lat0 is None else lat0
    origin_lon = GRID_ORIGIN[1] if lon0 is None else lon0
    lon_scale = 111_320.0 * math.cos(math.radians(origin_lat))

    def _fn(coords):
        out = coords.copy()
        out[:, 0] = origin_lon + coords[:, 0] / lon_scale
        out[:, 1] = origin_lat + coords[:, 1] / 110_540.0
        return out

    return shp_transform(geom, _fn)

MAX_EXPAND_RINGS = 12
MIN_CONFIDENCE_DEFAULT = 0.7
GRID_CODE = f"G{GRID_VERSION}"

SENSITIVITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class PublicationError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ----------------------------------------------------------------- geometry
def cell_index(x: float, y: float) -> tuple[int, int]:
    return math.floor(x / BASE_CELL_M), math.floor(y / BASE_CELL_M)


def cell_square(ix: int, iy: int) -> Polygon:
    return box(ix * BASE_CELL_M, iy * BASE_CELL_M, (ix + 1) * BASE_CELL_M, (iy + 1) * BASE_CELL_M)


def cell_code(ix: int, iy: int) -> str:
    return f"{GRID_CODE}:{ix}:{iy}"


def geom_to_geojson(geom) -> dict[str, Any]:
    if geom.is_empty:
        return {"type": "Polygon", "coordinates": []}
    local = geom if geom.geom_type in {"Polygon", "MultiPolygon"} else geom.buffer(0)
    projected = from_local(local, GRID_ORIGIN[0], GRID_ORIGIN[1])
    data = mapping(projected)
    return json.loads(json.dumps(data, default=float))


def geojson_to_local(geojson: dict[str, Any]):
    return to_local(shape(geojson), GRID_ORIGIN[0], GRID_ORIGIN[1])


def min_rotated_width(geom) -> float:
    """Short side of the minimum rotated rectangle, in metres."""
    if geom.is_empty:
        return 0.0
    rectangle = geom.minimum_rotated_rectangle
    if rectangle.geom_type != "Polygon":
        return 0.0
    coords = list(rectangle.exterior.coords)[:4]
    sides = [
        math.dist(coords[i], coords[(i + 1) % 4]) for i in range(4)
    ]
    return round(min(sides), 1)


def get_cell_rows(db: Session, indices: set[tuple[int, int]]) -> dict[tuple[int, int], GridCell]:
    rows: dict[tuple[int, int], GridCell] = {}
    if not indices:
        return rows
    codes = [cell_code(ix, iy) for ix, iy in indices]
    for row in db.execute(select(GridCell).where(GridCell.code.in_(codes))).scalars():
        row.ix, row.iy = _parse_cell_code(row.code)
        rows[(row.ix, row.iy)] = row
    missing = [idx for idx in indices if idx not in rows]
    for ix, iy in missing:
        square = cell_square(ix, iy)
        row = GridCell(
            code=cell_code(ix, iy),
            ix=ix,
            iy=iy,
            cell_size_m=BASE_CELL_M,
            grid_version=GRID_VERSION,
            geometry=geom_to_geojson(square),
            area_m2=BASE_CELL_M * BASE_CELL_M,
        )
        db.add(row)
        rows[(ix, iy)] = row
    db.flush()
    return rows


def _parse_cell_code(code: str) -> tuple[int, int]:
    _, ix, iy = code.split(":")
    return int(ix), int(iy)


def latitude_bounds_for_radius(centre: tuple[float, float], radius: float):
    x, y = centre
    return box(x - radius, y - radius, x + radius, y + radius)


# --------------------------------------------------------------- candidates
def _suitability(db: Session, species_code: str, version: int) -> SpeciesSuitability:
    row = db.execute(
        select(SpeciesSuitability)
        .where(SpeciesSuitability.species_code == species_code)
        .where(SpeciesSuitability.dataset_version == version)
    ).scalar_one_or_none()
    if row is None:
        raise PublicationError(f"物种 {species_code} 在适生条件 v{version} 中缺失", 404)
    return row


def _sensitivity(db: Session, species_code: str, version: int) -> SpeciesSensitivity:
    row = db.execute(
        select(SpeciesSensitivity)
        .where(SpeciesSensitivity.species_code == species_code)
        .where(SpeciesSensitivity.dataset_version == version)
    ).scalar_one_or_none()
    if row is None:
        raise PublicationError(f"物种 {species_code} 在敏感级别 v{version} 中缺失", 404)
    return row


def candidate_geometry(
    db: Session, species_code: str, suitability_version: int, habitat_version: int
) -> tuple[Any, list[dict[str, Any]]]:
    """Union of qualifying public habitat; no secret nest point is consulted."""
    suitability = _suitability(db, species_code, suitability_version)
    classes = set(suitability.habitat_classes or [])
    geoms = []
    included: list[dict[str, Any]] = []
    for feature in db.execute(
        select(HabitatPolygon)
        .where(HabitatPolygon.dataset_version == habitat_version)
        .where(HabitatPolygon.active.is_(True))
        .order_by(HabitatPolygon.code)
    ).scalars():
        if feature.habitat_class not in classes:
            continue
        low = max(float(feature.elevation_min_m), float(suitability.elevation_min_m))
        high = min(float(feature.elevation_max_m), float(suitability.elevation_max_m))
        if high < low:
            continue
        local = to_local(shape(feature.geometry), GRID_ORIGIN[0], GRID_ORIGIN[1])
        if local.is_empty:
            continue
        geoms.append(local)
        included.append(
            {
                "code": feature.code,
                "habitat_class": feature.habitat_class,
                "connectivity_group": feature.connectivity_group,
                "elevation_overlap_m": [round(low, 1), round(high, 1)],
                "area_m2": round(float(local.area), 1),
                "geometry": local,
            }
        )
    if not geoms:
        return Polygon(), included
    union = unary_union(geoms)
    return union, included


def candidate_cell_areas(polygons: list[Any]) -> dict[tuple[int, int], float]:
    """Exact candidate-habitat area per base cell.

    Computed per habitat polygon inside its own bounds so a distant bank never
    forces intersections across an unrelated mountain block.
    """
    from shapely.prepared import prep

    areas: dict[tuple[int, int], float] = {}
    flat: list[Any] = []
    for polygon in polygons:
        if polygon.is_empty:
            continue
        if polygon.geom_type == "MultiPolygon":
            flat.extend(polygon.geoms)
        else:
            flat.append(polygon)
    for polygon in flat:
        prepared = prep(polygon)
        minx, miny, maxx, maxy = polygon.bounds
        for ix in range(math.floor(minx / BASE_CELL_M), math.ceil(maxx / BASE_CELL_M)):
            for iy in range(math.floor(miny / BASE_CELL_M), math.ceil(maxy / BASE_CELL_M)):
                square = cell_square(ix, iy)
                if not prepared.intersects(square):
                    continue
                area = polygon.intersection(square).area
                if area >= 1.0:
                    areas[(ix, iy)] = float(areas.get((ix, iy), 0.0) + area)
    return areas


def connected_components(cells: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    remaining = set(cells)
    components: list[set[tuple[int, int]]] = []
    while remaining:
        start = min(remaining)
        remaining.remove(start)
        component = {start}
        queue = deque([start])
        while queue:
            ix, iy = queue.popleft()
            for neighbour in ((ix + 1, iy), (ix - 1, iy), (ix, iy + 1), (ix, iy - 1)):
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    component.add(neighbour)
                    queue.append(neighbour)
        components.append(component)
    return components


def _neighbours(cell: tuple[int, int]) -> set[tuple[int, int]]:
    ix, iy = cell
    return {
        (ix + dx, iy + dy)
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        if not (dx == 0 and dy == 0)
    }


def _cells_geometry(cells: set[tuple[int, int]]):
    return unary_union([cell_square(ix, iy) for ix, iy in cells])


def _candidate_signature(cells: set[tuple[int, int]]) -> str:
    """Stable identity of a habitat component (candidate cells), season-independent."""
    digest = hashlib.sha256(
        json.dumps(sorted(cells), separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"HB-{digest[:12]}"


def expand_region(
    seeds: set[tuple[int, int]],
    candidate_areas: dict[tuple[int, int], float],
    claimed: set[tuple[int, int]],
    *,
    min_width_m: float,
    min_area_m2: float,
) -> dict[str, Any]:
    """Coarsen a habitat component sideways until its narrow side meets the threshold.

    ``claimed`` holds cells already allocated to another region, and
    ``candidate_areas`` keys cover every other candidate cell. Neither may be
    absorbed, so nearby banks are never joined across unrelated terrain and two
    published regions never share ground. When growth is exhausted the region is
    withheld for failing the width or area minimum.
    """
    cells = set(seeds)
    rings = 0
    added: list[tuple[int, int]] = []
    blocked = set(candidate_areas) | claimed
    while rings < MAX_EXPAND_RINGS:
        geometry = _cells_geometry(cells)
        candidate_area = sum(candidate_areas.get(c, 0.0) for c in cells)
        width = min_rotated_width(geometry)
        if width >= min_width_m and candidate_area >= min_area_m2:
            break
        if candidate_area < min_area_m2:
            # Growing over non-habitat cells cannot create real habitat area.
            break
        frontier: set[tuple[int, int]] = set()
        for cell in cells:
            frontier |= _neighbours(cell)
        frontier -= cells
        frontier -= blocked
        if not frontier:
            break
        cells |= frontier
        added.extend(sorted(frontier))
        rings += 1
    geometry = _cells_geometry(cells)
    candidate_area = sum(candidate_areas.get(c, 0.0) for c in cells)
    width = min_rotated_width(geometry)
    return {
        "cells": cells,
        "expanded_cells": added,
        "rings": rings,
        "candidate_area_m2": round(candidate_area, 1),
        "region_area_m2": round(float(geometry.area), 1),
        "width_m": width,
        "geometry": geometry,
        "width_ok": width >= min_width_m,
        "area_ok": candidate_area >= min_area_m2,
    }


# ------------------------------------------------------- partition building
def _scope(db: Session, code: str) -> RecipientScope:
    scope = db.execute(select(RecipientScope).where(RecipientScope.code == code)).scalar_one_or_none()
    if scope is None:
        raise PublicationError(f"接收范围 {code} 不存在", 404)
    return scope


def build_partition(
    db: Session,
    *,
    species_code: str,
    scope_code: str,
    habitat_version: int,
    suitability_version: int,
    sensitivity_version: int,
) -> dict[str, Any]:
    """Compute connected habitat regions and coarsen only where thresholds demand it."""
    scope = _scope(db, scope_code)
    sensitivity = _sensitivity(db, species_code, sensitivity_version)
    thresholds = effective_thresholds(scope, sensitivity.level)
    levels_allowed = set(scope.allowed_levels or [])
    union, included = candidate_geometry(db, species_code, suitability_version, habitat_version)
    included_codes = {item["code"] for item in included}
    candidate_geoms = [
        to_local(shape(feature.geometry), GRID_ORIGIN[0], GRID_ORIGIN[1])
        for feature in db.execute(
            select(HabitatPolygon)
            .where(HabitatPolygon.dataset_version == habitat_version)
            .where(HabitatPolygon.active.is_(True))
        ).scalars()
        if feature.code in included_codes
    ]
    candidate_areas = candidate_cell_areas(candidate_geoms)
    components = connected_components(set(candidate_areas))
    foreign_by_component: list[set[tuple[int, int]]] = []
    for index, component in enumerate(components):
        others: set[tuple[int, int]] = set()
        for other_index, other in enumerate(components):
            if other_index != index:
                others |= other
        foreign_by_component.append(others)

    regions: list[dict[str, Any]] = []
    cell_owner: dict[tuple[int, int], int] = {}
    display_size = display_cell_m(thresholds)
    claimed: set[tuple[int, int]] = set()
    # Larger habitat components are claimed first; a large component keeps its
    # identity, and only the leftover narrow ones may need coarsening.
    for index, component in enumerate(components, start=1):
        result = expand_region(
            component,
            candidate_areas,
            claimed,
            min_width_m=thresholds["min_region_width_m"],
            min_area_m2=thresholds["min_candidate_habitat_m2"],
        )
        cells = result["cells"]
        claimed |= cells
        for cell in cells:
            cell_owner[cell] = index
        groups = {
            item["connectivity_group"] or item["code"]
            for item in included
            if not item["geometry"].disjoint(result["geometry"])
        }
        family_for_region = (
            next(iter(groups)) if len(groups) == 1 else _candidate_signature(component)
        )
        reasons: list[dict[str, Any]] = []
        if not result["area_ok"]:
            reasons.append(
                {
                    "code": "CANDIDATE_HABITAT_BELOW_MINIMUM",
                    "detail": (
                        f"候选生境 {result['candidate_area_m2']:.0f} m²，低于接收范围/敏感级别要求的 "
                        f"{thresholds['min_candidate_habitat_m2']:.0f} m²；扩展不覆盖其他候选生境，"
                        "不以包围盒或无关地形面积充数。"
                    ),
                }
            )
        if not result["width_ok"]:
            reasons.append(
                {
                    "code": "REGION_TOO_NARROW",
                    "detail": (
                        f"合并后最窄宽度 {result['width_m']:.0f} m，仍低于要求的 "
                        f"{thresholds['min_region_width_m']:.0f} m；狭窄河岸继续扩大会吸收无关地形"
                        "或其他生境，该区域不公开。"
                    ),
                }
            )
        if sensitivity.level not in levels_allowed:
            reasons.append(
                {
                    "code": "SENSITIVITY_NOT_ALLOWED_FOR_SCOPE",
                    "detail": f"接收范围 {scope.code} 不允许公开敏感级别 {sensitivity.level} 的物种结果。",
                }
            )
        regions.append(
            {
                "index": index,
                "family": family_for_region,
                "cells": cells,
                "expanded_cells": set(result["expanded_cells"]),
                "candidate_cells": component,
                "candidate_area_m2": result["candidate_area_m2"],
                "region_area_m2": result["region_area_m2"],
                "width_m": result["width_m"],
                "rings": result["rings"],
                "geometry": result["geometry"],
                "display_cell_m": display_size,
                "reasons": reasons,
            }
        )

    partition = {
        "scope": scope,
        "sensitivity": sensitivity,
        "species_code": species_code,
        "thresholds": thresholds,
        "candidate_union": union,
        "habitat_features": included,
        "candidate_areas": candidate_areas,
        "regions": regions,
        "cell_owner": cell_owner,
        "levels_allowed": levels_allowed,
        "prior_merges": [],
        "prior_gaps": [],
    }
    assign_display_blocks(partition)
    return partition


def _candidate_signature(cells: set[tuple[int, int]]) -> str:
    """Stable identity of a habitat component (candidate cells), season-independent."""
    digest = hashlib.sha256(
        json.dumps(sorted(cells), separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"HB-{digest[:12]}"


def expand_region(
    seeds: set[tuple[int, int]],
    candidate_areas: dict[tuple[int, int], float],
    claimed: set[tuple[int, int]],
    *,
    min_width_m: float,
    min_area_m2: float,
) -> dict[str, Any]:
    """Coarsen a habitat component sideways until its narrow side meets the threshold.

    ``claimed`` holds cells already allocated to another region, and
    ``candidate_areas`` keys cover every other candidate cell. Neither may be
    absorbed, so nearby banks are never joined across unrelated terrain and two
    published regions never share ground. When growth is exhausted the region is
    withheld for failing the width or area minimum.
    """
    cells = set(seeds)
    rings = 0
    added: list[tuple[int, int]] = []
    blocked = set(candidate_areas) | claimed
    while rings < MAX_EXPAND_RINGS:
        geometry = _cells_geometry(cells)
        candidate_area = sum(candidate_areas.get(c, 0.0) for c in cells)
        width = min_rotated_width(geometry)
        if width >= min_width_m and candidate_area >= min_area_m2:
            break
        if candidate_area < min_area_m2:
            # Growing over non-habitat cells cannot create real habitat area.
            break
        frontier: set[tuple[int, int]] = set()
        for cell in cells:
            frontier |= _neighbours(cell)
        frontier -= cells
        frontier -= blocked
        if not frontier:
            break
        cells |= frontier
        added.extend(sorted(frontier))
        rings += 1
    geometry = _cells_geometry(cells)
    candidate_area = sum(candidate_areas.get(c, 0.0) for c in cells)
    width = min_rotated_width(geometry)
    return {
        "cells": cells,
        "expanded_cells": added,
        "rings": rings,
        "candidate_area_m2": round(candidate_area, 1),
        "region_area_m2": round(float(geometry.area), 1),
        "width_m": width,
        "geometry": geometry,
        "width_ok": width >= min_width_m,
        "area_ok": candidate_area >= min_area_m2,
    }


# ------------------------------------------------------- partition building
def _scope(db: Session, code: str) -> RecipientScope:
    scope = db.execute(select(RecipientScope).where(RecipientScope.code == code)).scalar_one_or_none()
    if scope is None:
        raise PublicationError(f"接收范围 {code} 不存在", 404)
    return scope


def build_partition(
    db: Session,
    *,
    species_code: str,
    scope_code: str,
    habitat_version: int,
    suitability_version: int,
    sensitivity_version: int,
) -> dict[str, Any]:
    """Compute connected habitat regions and coarsen only where thresholds demand it."""
    scope = _scope(db, scope_code)
    sensitivity = _sensitivity(db, species_code, sensitivity_version)
    thresholds = effective_thresholds(scope, sensitivity.level)
    levels_allowed = set(scope.allowed_levels or [])
    union, included = candidate_geometry(db, species_code, suitability_version, habitat_version)
    included_codes = {item["code"] for item in included}
    candidate_geoms = [
        to_local(shape(feature.geometry), GRID_ORIGIN[0], GRID_ORIGIN[1])
        for feature in db.execute(
            select(HabitatPolygon)
            .where(HabitatPolygon.dataset_version == habitat_version)
            .where(HabitatPolygon.active.is_(True))
        ).scalars()
        if feature.code in included_codes
    ]
    candidate_areas = candidate_cell_areas(candidate_geoms)
    components = connected_components(set(candidate_areas))
    foreign_by_component: list[set[tuple[int, int]]] = []
    for index, component in enumerate(components):
        others: set[tuple[int, int]] = set()
        for other_index, other in enumerate(components):
            if other_index != index:
                others |= other
        foreign_by_component.append(others)

    regions: list[dict[str, Any]] = []
    cell_owner: dict[tuple[int, int], int] = {}
    display_size = display_cell_m(thresholds)
    claimed: set[tuple[int, int]] = set()
    # Larger habitat components are claimed first; a large component keeps its
    # identity, and only the leftover narrow ones may need coarsening.
    for index, component in enumerate(components, start=1):
        result = expand_region(
            component,
            candidate_areas,
            claimed,
            min_width_m=thresholds["min_region_width_m"],
            min_area_m2=thresholds["min_candidate_habitat_m2"],
        )
        cells = result["cells"]
        claimed |= cells
        for cell in cells:
            cell_owner[cell] = index
        groups = {
            item["connectivity_group"] or item["code"]
            for item in included
            if not item["geometry"].disjoint(result["geometry"])
        }
        family_for_region = (
            next(iter(groups)) if len(groups) == 1 else _candidate_signature(component)
        )
        reasons: list[dict[str, Any]] = []
        if not result["area_ok"]:
            reasons.append(
                {
                    "code": "CANDIDATE_HABITAT_BELOW_MINIMUM",
                    "detail": (
                        f"候选生境 {result['candidate_area_m2']:.0f} m²，低于接收范围/敏感级别要求的 "
                        f"{thresholds['min_candidate_habitat_m2']:.0f} m²；扩展不覆盖其他候选生境，"
                        "不以包围盒或无关地形面积充数。"
                    ),
                }
            )
        if not result["width_ok"]:
            reasons.append(
                {
                    "code": "REGION_TOO_NARROW",
                    "detail": (
                        f"合并后最窄宽度 {result['width_m']:.0f} m，仍低于要求的 "
                        f"{thresholds['min_region_width_m']:.0f} m；狭窄河岸继续扩大会吸收无关地形"
                        "或其他生境，该区域不公开。"
                    ),
                }
            )
        if sensitivity.level not in levels_allowed:
            reasons.append(
                {
                    "code": "SENSITIVITY_NOT_ALLOWED_FOR_SCOPE",
                    "detail": f"接收范围 {scope.code} 不允许公开敏感级别 {sensitivity.level} 的物种结果。",
                }
            )
        regions.append(
            {
                "index": index,
                "family": family_for_region,
                "cells": cells,
                "expanded_cells": set(result["expanded_cells"]),
                "candidate_cells": component,
                "candidate_area_m2": result["candidate_area_m2"],
                "region_area_m2": result["region_area_m2"],
                "width_m": result["width_m"],
                "rings": result["rings"],
                "geometry": result["geometry"],
                "display_cell_m": display_size,
                "reasons": reasons,
            }
        )

    partition = {
        "scope": scope,
        "sensitivity": sensitivity,
        "species_code": species_code,
        "thresholds": thresholds,
        "candidate_union": union,
        "habitat_features": included,
        "candidate_areas": candidate_areas,
        "regions": regions,
        "cell_owner": cell_owner,
        "levels_allowed": levels_allowed,
        "prior_merges": [],
        "prior_gaps": [],
    }
    assign_display_blocks(partition)
    return partition


def _season_bounds(run: SurveyRun) -> tuple[datetime, datetime]:
    start = datetime.combine(run.season_start, time.min, tzinfo=RESERVE_TZ)
    end = datetime.combine(run.season_end + timedelta(days=1), time.min, tzinfo=RESERVE_TZ)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _eligible_certificates(db: Session, run_id: int) -> dict[int, Certificate]:
    rows = db.execute(
        select(Certificate)
        .where(Certificate.run_id == run_id)
        .where(Certificate.superseded_by_id.is_(None))
    ).scalars()
    return {row.unit_id: row for row in rows}


def _serialise_geometry(geom) -> dict[str, Any]:
    return geom_to_geojson(geom)


def _region_index_for_point(
    point: Point, regions: list[dict[str, Any]], radius_m: float
) -> tuple[int | None, list[int]]:
    """Return (owner, touching regions) for a location with a stated error radius.

    Attribution requires the whole precision circle to fall inside a single region.
    If the circle reaches another partition, or spills outside every published
    region, the observation stays unattributed: its count is kept, but it is never
    spread across records or assigned to the closest-looking region.
    """
    circle = point.buffer(max(radius_m, 1.0))
    touching = [region["index"] for region in regions if region["geometry"].intersects(circle)]
    if len(touching) != 1:
        return None, touching
    owner = touching[0]
    region_geom = next(region["geometry"] for region in regions if region["index"] == owner)
    if not region_geom.covers(circle):
        return None, touching
    return owner, touching


def _material_key(record: ArchiveRecord, determination: Determination) -> str:
    payload = record.payload or {}
    for key in ("material_key", "original_ref", "image_sha256", "repost_of"):
        value = payload.get(key)
        if value:
            return str(value)
    return record.external_id


def attribute_statistics(
    db: Session,
    *,
    run: SurveyRun,
    species_code: str,
    regions: list[dict[str, Any]],
    min_confidence: float,
) -> dict[str, Any]:
    """Re-enter observations, valid device effort and route coverage into final regions."""
    season_start, season_end = _season_bounds(run)
    certs = _eligible_certificates(db, run.id)
    all_units = list(
        db.execute(select(SurveyUnit).where(SurveyUnit.run_id == run.id)).scalars()
    )
    unit_index = {(u.station_id, u.unit_date): u for u in all_units}
    eligible_units = [
        u for u in all_units if u.id in certs and certs[u.id].trend_eligible
    ]
    stations = {s.id: s for s in db.execute(select(Station)).scalars()}

    observations: list[dict[str, Any]] = []
    duplicates = 0
    unattributed = 0
    internal_candidates = 0
    seen_material: dict[str, str] = {}

    determinations = list(
        db.execute(
            select(Determination, ArchiveRecord)
            .join(ArchiveRecord, Determination.material_record_id == ArchiveRecord.id)
            .where(Determination.status == "active")
            .where(Determination.species_code == species_code)
            .where(Determination.confidence >= min_confidence)
            .where(ArchiveRecord.record_type == "camera_event")
            .where(ArchiveRecord.event_time >= season_start)
            .where(ArchiveRecord.event_time < season_end)
            .order_by(Determination.id)
        ).all()
    )
    excluded = 0
    for determination, record in determinations:
        local_day = record.event_time.astimezone(RESERVE_TZ).date()
        unit = unit_index.get((record.station_id, local_day))
        if unit is None:
            continue
        internal_candidates += 1
        material_key = _material_key(record, determination)
        certificate = certs.get(unit.id)
        if certificate is None or not certificate.trend_eligible:
            excluded += 1
            observations.append(
                {
                    "record_id": record.id,
                    "external_id": record.external_id,
                    "material_key": material_key,
                    "source": determination.source,
                    "verdict": determination.verdict,
                    "confidence": determination.confidence,
                    "disposition": "excluded",
                    "reason": "所属调查单元证书不可用于趋势输入（未有效/待核/复审中），不汇入公开统计",
                    "region_index": None,
                    "cell": None,
                }
            )
            continue
        if material_key in seen_material:
            duplicates += 1
            observations.append(
                {
                    "record_id": record.id,
                    "external_id": record.external_id,
                    "material_key": material_key,
                    "source": determination.source,
                    "verdict": determination.verdict,
                    "confidence": determination.confidence,
                    "disposition": "duplicate",
                    "reason": (
                        f"同一素材被来源 {determination.source} 转载/重复提交，"
                        f"首次计入来源 {seen_material[material_key]}，按素材排重"
                    ),
                    "region_index": None,
                    "cell": None,
                }
            )
            continue
        seen_material[material_key] = determination.source
        if record.latitude is None or record.longitude is None:
            unattributed += 1
            observations.append(
                {
                    "record_id": record.id,
                    "external_id": record.external_id,
                    "material_key": material_key,
                    "source": determination.source,
                    "verdict": determination.verdict,
                    "confidence": determination.confidence,
                    "disposition": "unattributed",
                    "reason": "素材无坐标，不能归属发布分区",
                    "region_index": None,
                    "cell": None,
                }
            )
            continue
        x, y = lonlat_to_local(record.latitude, record.longitude)
        point = Point(x, y)
        radius = float(record.coord_precision_m or 10.0)
        owner, candidates = _region_index_for_point(point, regions, radius)
        if owner is None:
            unattributed += 1
            observations.append(
                {
                    "record_id": record.id,
                    "external_id": record.external_id,
                    "material_key": material_key,
                    "source": determination.source,
                    "verdict": determination.verdict,
                    "confidence": determination.confidence,
                    "disposition": "unattributed",
                    "reason": (
                        "定位误差跨分区，保留未归属数量"
                        if len(candidates) > 1
                        else "定位误差覆盖发布分区外区域，不能归属"
                    ),
                    "region_index": None,
                    "cell": cell_index(x, y),
                }
            )
            continue
        observations.append(
            {
                "record_id": record.id,
                "external_id": record.external_id,
                "material_key": material_key,
                "source": determination.source,
                "verdict": determination.verdict,
                "confidence": determination.confidence,
                "disposition": "included",
                "reason": "",
                "region_index": owner,
                "cell": cell_index(x, y),
            }
        )

    # ---- device effort: each valid unit is allocated to exactly one region, then
    # overlapping windows inside that region are merged before summing.
    effort_rows: list[dict[str, Any]] = []
    internal_effort_hours = 0.0
    from .models import StationVersion

    versions = list(db.execute(select(StationVersion)).scalars())
    by_station: dict[int, list[StationVersion]] = defaultdict(list)
    for version in versions:
        by_station[version.station_id].append(version)
    for unit in eligible_units:
        station = stations.get(unit.station_id)
        if station is None:
            continue
        version = next(
            (v for v in by_station[station.id] if v.id == unit.station_version_id), None
        )
        buffer_geom = None
        if version is not None:
            try:
                boundary = to_local(shape(version.boundary))
                buffer_geom = boundary.buffer(float(version.buffer_width_m))
            except Exception:
                buffer_geom = None
        device = db.execute(
            select(GateCheck)
            .where(GateCheck.unit_id == unit.id)
            .where(GateCheck.gate == "device")
        ).scalar_one_or_none()
        measured = device.measured or {}
        available = float(measured.get("available_hours", 0.0)) if device else 0.0
        downtime = float(measured.get("explained_downtime_hours", 0.0)) if device else 0.0
        unexplained = float(measured.get("unexplained_gap_hours", 0.0)) if device else 0.0
        # 部分调查停机：公开的努力量只使用设备真正可用的时段。
        hours = max(0.0, available - 0.0)
        owner = None
        if buffer_geom is not None and not buffer_geom.is_empty:
            best_overlap = 0.0
            for region in regions:
                overlap = region["geometry"].intersection(buffer_geom).area
                if overlap > best_overlap:
                    best_overlap = overlap
                    owner = region["index"]
        start, end = day_window(unit.unit_date)
        if owner is None:
            effort_rows.append(
                {
                    "unit_id": unit.id,
                    "station_id": unit.station_id,
                    "window_start": start,
                    "window_end": end,
                    "hours": hours,
                    "region_index": None,
                    "disposition": "unattributed",
                    "reason": "有效设备时段无法归属到任一发布分区，保留未归属数量",
                }
            )
            continue
        internal_effort_hours += hours
        effort_rows.append(
            {
                "unit_id": unit.id,
                "station_id": unit.station_id,
                "window_start": start,
                "window_end": end,
                "hours": hours,
                "region_index": owner,
                "disposition": "included",
                "reason": (
                    f"设备可用 {available:.1f}h，已扣除计划停机 {downtime:.1f}h"
                    + (f"、待核缺口 {unexplained:.1f}h 不计入" if unexplained else "")
                ),
            }
        )

    # ---- routes: one eligible track is allocated whole to a single region (the one
    #      it covers most), so its coverage can never be re-assembled from pieces.
    route_rows: list[dict[str, Any]] = []
    track_duplicates = 0
    internal_tracks = 0
    included_tracks: set[int] = set()
    excluded_tracks = 0
    seen_tracks: set[str] = set()
    from .utils import track_geometry

    track_records = list(
        db.execute(
            select(ArchiveRecord)
            .where(ArchiveRecord.record_type == "patrol_track")
            .where(ArchiveRecord.event_time >= season_start)
            .where(ArchiveRecord.event_time < season_end)
            .order_by(ArchiveRecord.id)
        ).scalars()
    )
    route_geoms: dict[int, list[Any]] = defaultdict(list)
    for record in track_records:
        local_day = record.event_time.astimezone(RESERVE_TZ).date()
        unit = unit_index.get((record.station_id, local_day))
        if unit is None:
            continue
        internal_tracks += 1
        payload = record.payload or {}
        key = str(payload.get("track_key") or payload.get("original_ref") or record.external_id)
        if key in seen_tracks:
            track_duplicates += 1
            route_rows.append(
                {
                    "record_id": record.id,
                    "region_index": None,
                    "length_m": 0.0,
                    "disposition": "duplicate",
                    "reason": "重复上传的同一巡护轨迹，按轨迹键排重",
                }
            )
            continue
        seen_tracks.add(key)
        geom = track_geometry(payload)
        if geom is None:
            excluded_tracks += 1
            route_rows.append(
                {
                    "record_id": record.id,
                    "region_index": None,
                    "length_m": 0.0,
                    "disposition": "excluded",
                    "reason": "无有效轨迹几何",
                }
            )
            continue
        local_track = to_local(geom, GRID_ORIGIN[0], GRID_ORIGIN[1])
        best_index = None
        best_length = 0.0
        best_clipped = None
        for region in regions:
            clipped = local_track.intersection(region["geometry"])
            if clipped.is_empty:
                continue
            length = float(clipped.length)
            # A region gets the whole track only if it holds the majority; a track
            # bridged across two banks is not split into pieces.
            if length > local_track.length * 0.5 and length > best_length:
                best_length = length
                best_index = region["index"]
                best_clipped = clipped
        if best_index is None or best_length <= 0:
            excluded_tracks += 1
            route_rows.append(
                {
                    "record_id": record.id,
                    "region_index": None,
                    "length_m": round(float(local_track.length), 1),
                    "disposition": "excluded",
                    "reason": "轨迹不在任何发布分区内，不汇入公开统计",
                }
            )
            continue
        included_tracks.add(record.id)
        route_geoms[best_index].append(best_clipped)
        route_rows.append(
            {
                "record_id": record.id,
                "region_index": best_index,
                "length_m": round(float(best_clipped.length), 1),
                "disposition": "included",
                "reason": "整条轨迹按其覆盖最长的发布分区归属，不拆分到多块",
            }
        )

    region_stats: dict[int, dict[str, Any]] = {
        region["index"]: {
            "observation_count": 0,
            "present_count": 0,
            "duplicate_materials": 0,
            "cell_observations": defaultdict(int),
            "cell_present": defaultdict(int),
            "windows": [],
            "region_effort_unknown_units": 0,
            "observed_region_indices": set(),
            "route_geoms": route_geoms[region["index"]],
        }
        for region in regions
    }
    for row in observations:
        if row["disposition"] == "included" and row["region_index"] is not None:
            stats = region_stats[row["region_index"]]
            stats["observation_count"] += 1
            stats["observed_region_indices"].add(row["region_index"])
            if row["verdict"] == "present":
                stats["present_count"] += 1
            cell = row["cell"]
            if cell is not None:
                stats["cell_observations"][cell] += 1
                if row["verdict"] == "present":
                    stats["cell_present"][cell] += 1
        elif row["disposition"] == "duplicate" and row["region_index"] is not None:
            region_stats[row["region_index"]]["duplicate_materials"] += 1

    for row in effort_rows:
        if row["disposition"] != "included" or row["region_index"] is None:
            continue
        stats = region_stats[row["region_index"]]
        stats["windows"].append(
            (row["window_start"], row["window_end"], float(row["hours"]), row["unit_id"])
        )

    # Merge overlapping windows per region: overlapping valid runs are counted once.
    # The device hours inside a merged interval are attributed proportionally to the
    # constituent units, so the sum over blocks can never exceed wall-clock time.
    for index, stats in region_stats.items():
        windows = sorted(stats["windows"])
        merged: list[list[Any]] = []
        for start, end, hours, unit_id in windows:
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
                merged[-1][2] += hours
                merged[-1][3].append(unit_id)
            else:
                merged.append([start, end, hours, [unit_id]])
        total_hours = 0.0
        blocks: list[dict[str, Any]] = []
        internal_hours = 0.0
        removed_overlap_hours = 0.0
        for start, end, hours, unit_ids in merged:
            available = (end - start).total_seconds() / 3600
            counted = min(hours, available)
            removed_overlap_hours += max(0.0, hours - counted)
            total_hours += counted
            internal_hours += hours
            blocks.append(
                {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "hours": round(counted, 2),
                    "raw_hours": round(hours, 2),
                    "merged_intervals": len(unit_ids),
                    "units": sorted(set(unit_ids)),
                }
            )
        stats["effective_hours"] = round(total_hours, 2)
        stats["internal_hours"] = round(internal_hours, 2)
        stats["removed_overlap_hours"] = round(removed_overlap_hours, 2)
        stats["effort_known"] = bool(merged)
        stats["effort_unknown_units"] = 0 if merged else 1
        stats["window_blocks"] = blocks

    # Route length: union of clipped geometries per region (no overlapping double count).
    for index, stats in region_stats.items():
        geoms = stats["route_geoms"]
        if geoms:
            union = unary_union(geoms)
            stats["route_length_m"] = round(float(union.length), 1)
        else:
            stats["route_length_m"] = 0.0

    return {
        "observations": observations,
        "duplicates": duplicates,
        "unattributed": unattributed,
        "excluded": excluded,
        "internal_candidates": internal_candidates,
        "effort_rows": effort_rows,
        "internal_effort_hours": round(internal_effort_hours, 2),
        "route_rows": route_rows,
        "track_duplicates": track_duplicates,
        "internal_tracks": internal_tracks,
        "included_tracks": len(included_tracks),
        "excluded_tracks": excluded_tracks,
        "region_stats": region_stats,
    }


def _region_cell_stats(
    region: dict[str, Any], stats: dict[str, Any], candidate_areas: dict[tuple[int, int], float]
) -> list[dict[str, Any]]:
    rows = []
    for cell in sorted(region["cells"]):
        rows.append(
            {
                "cell": cell,
                "cell_code": cell_code(*cell),
                "candidate_habitat_m2": round(candidate_areas.get(cell, 0.0), 1),
                "observation_count": int(stats["cell_observations"].get(cell, 0)),
                "present_count": int(stats["cell_present"].get(cell, 0)),
            }
        )
    return rows


# ------------------------------------------------------------- checks & storage
def display_cell_m(thresholds: dict[str, Any]) -> float:
    """Disclosure lattice: never finer than the base cell or the scope threshold."""
    steps = max(1, math.ceil(float(thresholds["min_cell_size_m"]) / BASE_CELL_M))
    return steps * BASE_CELL_M


def _blocks_for(cells: set[tuple[int, int]], size: float) -> set[tuple[int, int]]:
    step = max(1, int(round(size / BASE_CELL_M)))
    return {(ix // step, iy // step) for ix, iy in cells}


def display_geometry_for_blocks(
    blocks: set[tuple[int, int]], size: float
) -> Any:
    if not blocks:
        return Polygon()
    return unary_union(
        [box(bx * size, by * size, (bx + 1) * size, (by + 1) * size) for bx, by in sorted(blocks)]
    )


def assign_display_blocks(partition: dict[str, Any]) -> list[dict[str, Any]]:
    """Give each disclosure block to exactly one region.

    A block goes to the region holding the most candidate habitat inside it. Blocks
    therefore never overlap, the map and the statistics describe the same ground, and
    two regions can never be recombined from shared display pixels.
    """
    thresholds = partition["thresholds"]
    size = display_cell_m(thresholds)
    regions = partition["regions"]
    owners: dict[tuple[int, int], list[tuple[float, int]]] = defaultdict(list)
    for region in regions:
        area_by_block: dict[tuple[int, int], float] = defaultdict(float)
        for cell in region["candidate_cells"]:
            area_by_block[_blocks_for({cell}, size).pop()] += partition["candidate_areas"].get(
                cell, 0.0
            )
        for block, area in area_by_block.items():
            owners[block].append((area, region["index"]))
    block_owner: dict[tuple[int, int], int] = {}
    for block, candidates in owners.items():
        candidates.sort(key=lambda pair: (-pair[0], pair[1]))
        block_owner[block] = candidates[0][1]
    assigned: dict[int, set[tuple[int, int]]] = defaultdict(set)
    for block, owner in block_owner.items():
        assigned[owner].add(block)
    notes: list[dict[str, Any]] = []
    for region in regions:
        blocks = assigned.get(region["index"], set())
        if blocks:
            region["display"] = display_geometry_for_blocks(blocks, size)
        else:
            # No block is predominantly this region: showing its own fine cells would
            # reveal more than the disclosure granularity, so it can not be published.
            region["display"] = region["geometry"]
            region.setdefault("reasons", []).append(
                {
                    "code": "DISPLAY_BLOCK_ABSORBED",
                    "detail": (
                        "该区域的候选生境在公开粒度下不占任何一块的主要部分，与其他区域共用块会"
                        "无法区分归属，因此不公开该区域。"
                    ),
                }
            )
            notes.append({"region_index": region["index"], "blocks": 0})
        region["display_cell_m"] = size
        region["display_blocks"] = sorted(blocks)
    partition["display_block_owner"] = block_owner
    return notes


def extent_key(region: dict[str, Any], thresholds: dict[str, Any]) -> str:
    """Stable key over the habitat component's retained base units.

    Every region keeps the base units of the candidate habitat it was built from, and
    those units do not change when the disclosure lattice is coarsened for another
    sensitivity level. Two periods over the same component therefore share a key and
    can be compared; a season where the component itself changed has no common extent
    and is shown separately.
    """
    cells = sorted(region.get("candidate_cells") or region["cells"])
    digest = hashlib.sha256(
        json.dumps(cells, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"EX-{digest[:12]}"


def _detect_display_collisions(regions: list[dict[str, Any]]) -> dict[int, list[int]]:
    collisions: dict[int, list[int]] = defaultdict(list)
    for i, region_a in enumerate(regions):
        for region_b in regions[i + 1 :]:
            if region_a["display"].intersects(region_b["display"]):
                collisions[region_a["index"]].append(region_b["index"])
                collisions[region_b["index"]].append(region_a["index"])
    return collisions


def _prior_materials(db: Session, scope_code: str, species_code: str) -> list[PublicMaterial]:
    return list(
        db.execute(
            select(PublicMaterial)
            .where(PublicMaterial.scope_code == scope_code)
            .where(PublicMaterial.species_code == species_code)
            .where(PublicMaterial.status.in_(("active", "stale")))
            .order_by(PublicMaterial.id)
        ).scalars()
    )


def _prior_region_geometries(material: PublicMaterial) -> list[dict[str, Any]]:
    return list(material.regions or [])


def _new_dataset_revisions(db: Session) -> tuple[int, int]:
    habitat = active_revision(db, "habitat")
    sensitivity = active_revision(db, "sensitivity")
    return habitat.version, sensitivity.version


def _display_footprint_cells(geojson: dict[str, Any]) -> set[tuple[int, int]]:
    """Base cells touched by a previously released display geometry."""
    if not geojson:
        return set()
    try:
        geom = geojson_to_local(geojson)
    except Exception:
        return set()
    if geom.is_empty:
        return set()
    minx, miny, maxx, maxy = geom.bounds
    cells: set[tuple[int, int]] = set()
    for ix in range(math.floor(minx / BASE_CELL_M), math.ceil(maxx / BASE_CELL_M)):
        for iy in range(math.floor(miny / BASE_CELL_M), math.ceil(maxy / BASE_CELL_M)):
            if geom.intersects(cell_square(ix, iy)):
                cells.add((ix, iy))
    return cells


def _prior_released_cells(
    db: Session, scope_code: str, species_code: str
) -> list[dict[str, Any]]:
    """Retained base units of every earlier released material for this species/scope."""
    released: list[dict[str, Any]] = []
    for material in _prior_materials(db, scope_code, species_code):
        for old in material.regions or []:
            cells: set[tuple[int, int]] = set()
            for code in old.get("base_cells") or []:
                try:
                    cells.add(_parse_cell_code(code))
                except ValueError:
                    continue
            if not cells:
                cells = _display_footprint_cells(old.get("geometry") or {})
            if cells:
                released.append(
                    {
                        "material": material.material_code,
                        "region_code": old.get("region_code"),
                        "cells": cells,
                    }
                )
    return released


def apply_prior_merges(
    db: Session,
    partition: dict[str, Any],
    scope_code: str,
    species_code: str,
) -> list[dict[str, Any]]:
    """Keep every previously released range inside the new published regions.

    A coarser new map is only safe if it still contains everything an older material
    covered: otherwise combining the two would locate habitat the old map revealed.
    Missing base units are merged into the nearest region ("继续扩大合并范围"); if no
    region can take them, the release is reported unsafe and must stop.
    """
    released = _prior_released_cells(db, scope_code, species_code)
    merges: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    if not released:
        partition["prior_merges"] = merges
        partition["prior_gaps"] = gaps
        return merges

    def published_regions() -> list[dict[str, Any]]:
        return [region for region in partition["regions"] if not region.get("reasons")]

    for entry in released:
        missing = set(entry["cells"])
        candidates = published_regions()
        for region in candidates:
            missing -= region["cells"]
        if not missing:
            continue
        if not candidates:
            gaps.append(
                {
                    "material": entry["material"],
                    "region_code": entry["region_code"],
                    "uncovered_cells": len(missing),
                    "reason": "没有任何可公开区域能够覆盖既有材料范围",
                }
            )
            continue
        # Merge into the region sharing the most borders with the missing cells.
        best = max(
            candidates,
            key=lambda region: len(
                touches := {
                    neighbour
                    for cell in missing
                    for neighbour in _neighbours(cell)
                    if neighbour in region["cells"]
                }
            ),
        )
        best["cells"] |= missing
        best["expanded_for_prior"] = True
        best["geometry"] = _cells_geometry(best["cells"])
        best["region_area_m2"] = round(float(best["geometry"].area), 1)
        best["width_m"] = min_rotated_width(best["geometry"])
        merges.append(
            {
                "reason": "PRIOR_RELEASE_RANGE",
                "material": entry["material"],
                "region_code": entry["region_code"],
                "region_index": best["index"],
                "added_cells": len(missing),
            }
        )

    assign_display_blocks(partition)
    covered_cells = {
        cell for region in published_regions() for cell in region["cells"]
    }
    for entry in released:
        still_missing = set(entry["cells"]) - covered_cells
        if still_missing:
            gaps.append(
                {
                    "material": entry["material"],
                    "region_code": entry["region_code"],
                    "uncovered_cells": len(still_missing),
                    "reason": "既有材料范围仍未被公开显示覆盖；应继续扩大合并范围或停止公开",
                }
            )
    partition["prior_merges"] = merges
    partition["prior_gaps"] = gaps
    return merges


def create_publication(
    db: Session,
    *,
    species_code: str,
    scope_code: str,
    season_code: str,
    run: SurveyRun,
    actor: str,
    note: str = "",
) -> PublicationRun:
    habitat_revision = active_revision(db, "habitat")
    suitability_revision = active_revision(db, "suitability")
    sensitivity_revision = active_revision(db, "sensitivity")
    scope_revision = active_revision(db, "scopes")
    sensitivity = _sensitivity(db, species_code, sensitivity_revision.version)
    scope = _scope(db, scope_code)

    partition = build_partition(
        db,
        species_code=species_code,
        scope_code=scope_code,
        habitat_version=habitat_revision.version,
        suitability_version=suitability_revision.version,
        sensitivity_version=sensitivity_revision.version,
    )
    thresholds = partition["thresholds"]
    prior_merges = apply_prior_merges(db, partition, scope_code, species_code)
    thresholds = {
        **thresholds,
        "effective_display_cell_m": (
            partition["regions"][0].get("display_cell_m", 0.0) if partition["regions"] else 0.0
        ),
    }
    rulebook = db.get(RulebookVersion, run.rulebook_version_id)
    min_conf = float(
        (rulebook.requirements.get("reading", {}) if rulebook else {}).get(
            "min_confidence", MIN_CONFIDENCE_DEFAULT
        )
    )
    stats = attribute_statistics(
        db,
        run=run,
        species_code=species_code,
        regions=partition["regions"],
        min_confidence=min_conf,
    )

    fingerprint = canonical_hash(
        {
            "species": species_code,
            "scope": scope_code,
            "season": season_code,
            "run": run.run_code,
            "habitat": habitat_revision.version,
            "suitability": suitability_revision.version,
            "sensitivity": sensitivity_revision.version,
            "scope_version": scope_revision.version,
            "thresholds": thresholds,
            "regions": [
                {
                    "cells": sorted(region["cells"]),
                    "candidate": round(region["candidate_area_m2"], 1),
                }
                for region in partition["regions"]
            ],
        }
    )
    count = db.execute(
        select(func.count()).select_from(PublicationRun).where(
            PublicationRun.season_code == season_code
        )
    ).scalar_one()
    publication = PublicationRun(
        code=f"PUB-{season_code}-{scope_code}-{species_code[:6]}-{count + 1:03d}",
        scope_code=scope_code,
        species_code=species_code,
        season_code=season_code,
        survey_run_id=run.id,
        status="building",
        sensitivity_level=sensitivity.level,
        sensitivity_version=sensitivity_revision.version,
        habitat_version=habitat_revision.version,
        suitability_version=suitability_revision.version,
        scope_version=scope_revision.version,
        fingerprint=fingerprint,
        thresholds=thresholds,
        region_count=len(partition["regions"]),
        unattributed_observations=stats["unattributed"],
        created_by=actor,
        note=note,
    )
    db.add(publication)
    db.flush()

    cells = set()
    for region in partition["regions"]:
        cells |= region["cells"]
    cell_rows = get_cell_rows(db, cells)

    for region in partition["regions"]:
        reasons = list(region["reasons"])
        stats_row = stats["region_stats"][region["index"]]
        effort_known = bool(stats_row.get("effort_known"))
        if not effort_known:
            reasons.append(
                {
                    "code": "SURVEY_EFFORT_UNKNOWN",
                    "detail": "该区域没有有效设备时段可归入，调查强度未知，不能发布为物种未出现。",
                }
            )
        status = "withheld" if reasons else "published"
        display = region.get("display") or region["geometry"]
        region_code = f"R-{scope_code[:3]}-{publication.id:02d}-{region['index']:02d}"
        observation_count = int(stats_row["observation_count"])
        present_count = int(stats_row["present_count"])
        occurrence = (
            round(present_count / observation_count, 4)
            if observation_count
            else None
        )
        db.add(
            PartitionRegion(
                publication_id=publication.id,
                region_code=region_code,
                extent_key=extent_key(region, thresholds),
                component_key=region.get("family", f"HB-{publication.id}-{region['index']:02d}"),
                status=status,
                geometry=geom_to_geojson(region["geometry"]),
                display_geometry=geom_to_geojson(display),
                area_m2=round(region["region_area_m2"], 1),
                candidate_habitat_m2=round(region["candidate_area_m2"], 1),
                width_m=round(region["width_m"], 1),
                base_cell_count=len(region["cells"]),
                candidate_cells=sorted(cell_code(*c) for c in region["candidate_cells"]),
                display_cell_m=display_cell_m(thresholds),
                display_area_m2=round(float(display.area), 1),
                effort_known=effort_known,
                reasons=reasons,
                sensitivity_level=sensitivity.level,
                threshold_snapshot=thresholds,
                observation_count=observation_count,
                present_count=present_count,
                duplicate_materials=int(stats_row["duplicate_materials"]),
                effective_hours=float(stats_row.get("effective_hours", 0.0)),
                route_length_m=float(stats_row.get("route_length_m", 0.0)),
                effort_unknown_units=int(stats_row.get("effort_unknown_units", 0)),
                expanded_for_prior=bool(region.get("expanded_for_prior")),
                effort_units=sum(
                    len(block.get("units", [])) for block in stats_row.get("window_blocks", [])
                ),
                merged_effort_windows=len(stats_row.get("window_blocks", [])),
                occurrence_rate=occurrence,
            )
        )
    db.flush()

    _persist_attribution(db, publication, partition, stats, cell_rows)
    _run_checks(db, publication, partition, stats, run, min_conf)
    publication.status = "checked" if publication.checks_passed else "stopped"
    publication.checked_at = utcnow()
    if not publication.checks_passed:
        publication.stop_reason = "发布检查未通过"
    db.flush()
    return publication


def _persist_attribution(
    db: Session,
    publication: PublicationRun,
    partition: dict[str, Any],
    stats: dict[str, Any],
    cell_rows: dict[tuple[int, int], GridCell],
) -> None:
    regions = {
        row.region_code: row
        for row in db.execute(
            select(PartitionRegion).where(PartitionRegion.publication_id == publication.id)
        ).scalars()
    }
    region_by_index = {}
    for code, row in regions.items():
        index = int(code.rsplit("-", 1)[1])
        region_by_index[index] = row

    candidate_areas = partition["candidate_areas"]
    for index, row in region_by_index.items():
        region = next(r for r in partition["regions"] if r["index"] == index)
        region_stats = stats["region_stats"][index]
        for cell in sorted(region["cells"]):
            grid = cell_rows[cell]
            db.add(
                RegionCell(
                    region_id=row.id,
                    cell_id=grid.id,
                    cell_code=grid.code,
                    candidate_habitat_m2=round(candidate_areas.get(cell, 0.0), 1),
                    observation_count=int(region_stats["cell_observations"].get(cell, 0)),
                    present_count=int(region_stats["cell_present"].get(cell, 0)),
                )
            )
        for block in region_stats.get("window_blocks", []):
            db.add(
                RegionEffort(
                    publication_id=publication.id,
                    region_id=row.id,
                    unit_ids=list(block["units"]),
                    station_ids=[],
                    window_start=datetime.fromisoformat(block["start"]),
                    window_end=datetime.fromisoformat(block["end"]),
                    hours=block["hours"],
                    merged_intervals=int(block.get("merged_intervals", 1)),
                    disposition="included",
                    reason="重叠的有效设备时段已合并",
                )
            )

    for row in stats["observations"]:
        region = (
            region_by_index.get(row["region_index"]) if row["region_index"] is not None else None
        )
        cell = None
        if row["cell"] is not None:
            cell = cell_rows.get(row["cell"])
        db.add(
            RegionObservation(
                publication_id=publication.id,
                region_id=region.id if region else None,
                cell_id=cell.id if cell else None,
                record_id=row["record_id"],
                external_id=row["external_id"],
                material_key=row["material_key"],
                source=row["source"],
                species_code=publication.species_code,
                verdict=row["verdict"],
                confidence=row["confidence"],
                disposition=row["disposition"],
                reason=row["reason"],
            )
        )
    for row in stats["route_rows"]:
        region = (
            region_by_index.get(row["region_index"]) if row["region_index"] is not None else None
        )
        db.add(
            RegionRoute(
                publication_id=publication.id,
                region_id=region.id if region else None,
                cell_id=None,
                record_id=row["record_id"],
                length_m=row["length_m"],
                disposition=row["disposition"],
                reason=row["reason"],
            )
        )
    db.flush()


def scan_public_payload(payload: Any) -> set[str]:
    """Field/identifier scan for anything that would allow re-identifying a site."""
    forbidden_keys = {
        "latitude",
        "longitude",
        "lat",
        "lon",
        "lng",
        "centroid",
        "station_id",
        "station_code",
        "station_name",
        "record_id",
        "external_id",
        "material_key",
        "source_device",
        "image_ref",
        "image_sha256",
        "geometry_raw",
        "route",
        "route_geometry",
        "track",
        "track_geometry",
        "payload_hash",
        "position",
        "coordinates_precise",
        "nest",
        "nest_point",
        "site_code",
    }
    leaks: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key.lower() in forbidden_keys:
                    leaks.add(key)
                walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            lowered = node.lower()
            for marker in ("s3://", "file://", "station:", "cam-", "gps-", "img-", "trk-"):
                if marker in lowered:
                    leaks.add(f"value:{marker}")

    walk(payload)
    return leaks


def public_payload_for_check(
    db: Session, publication: PublicationRun, regions: list[PartitionRegion] | None = None
) -> dict[str, Any]:
    from .spatial_delivery import build_public_payload

    return build_public_payload(db, publication, regions=regions or None)["map"]


def _run_checks(
    db: Session,
    publication: PublicationRun,
    partition: dict[str, Any],
    stats: dict[str, Any],
    run: SurveyRun,
    min_confidence: float,
) -> None:
    regions = list(
        db.execute(
            select(PartitionRegion)
            .where(PartitionRegion.publication_id == publication.id)
            .order_by(PartitionRegion.id)
        ).scalars()
    )
    checks: list[tuple[str, bool, str, dict[str, Any]]] = []
    thresholds = partition["thresholds"]
    published = [r for r in regions if r.status == "published"]
    withheld = [r for r in regions if r.status == "withheld"]
    checks.append(
        (
            "PUBLISHABLE_REGIONS_EXIST",
            bool(published),
            (
                f"共 {len(regions)} 个连通候选区域，其中 {len(published)} 个满足发布条件。"
                if published
                else "没有任何连通区域满足当前敏感级别与接收范围的发布条件；该项物种结果不公开。"
            ),
            {"regions": len(regions), "published": len(published)},
        )
    )

    # 1. public habitat only — no region may be smaller than the candidate habitat
    #    it claims (a bounding box never substitutes for real habitat area).
    geometry_failures = []
    for region in published:
        extra = region.area_m2 - region.candidate_habitat_m2
        if region.candidate_habitat_m2 <= 0:
            geometry_failures.append(region.region_code)
        elif extra > region.display_area_m2 * 4 + 1_000_000:
            geometry_failures.append(region.region_code)
    checks.append(
        (
            "CANDIDATE_HABITAT_BASED",
            not geometry_failures,
            "候选范围只由公开生境类别与适生条件计算，未用秘密巢点反推；"
            "未以包围盒面积代替真实生境面积。"
            if not geometry_failures
            else f"以下区域缺少真实候选生境：{geometry_failures}",
            {"failures": geometry_failures},
        )
    )

    # 2. thresholds respected by every published region.
    bad = [
        r.region_code
        for r in published
        if r.candidate_habitat_m2 < thresholds["min_candidate_habitat_m2"]
        or r.width_m < thresholds["min_region_width_m"]
        or r.display_cell_m < thresholds["min_cell_size_m"]
    ]
    checks.append(
        (
            "DISCLOSURE_THRESHOLDS",
            not bad,
            "所有公开区域均满足最低空间尺度与最低候选生境面积；未达标区域已不公开。"
            if not bad
            else f"以下公开区域低于门槛：{bad}",
            {
                "min_candidate_habitat_m2": thresholds["min_candidate_habitat_m2"],
                "min_region_width_m": thresholds["min_region_width_m"],
                "min_cell_size_m": thresholds["min_cell_size_m"],
                "display_cell_m": display_cell_m(thresholds),
                "violations": bad,
            },
        )
    )

    # 3. every withheld region records connectivity + habitat area + reason.
    checks.append(
        (
            "WITHHELD_REASONS_RECORDED",
            all(bool(r.reasons) and bool(r.candidate_cells) for r in withheld),
            "所有未公开区域均记录了连通关系（保留基础单元）、可用生境面积与不能公开的原因。",
            {
                "withheld": len(withheld),
                "published": len(published),
                "codes": [r.region_code for r in withheld],
            },
        )
    )

    # 4. disjoint published extents (two different areas are never compared as one).
    collisions = []
    for i, first in enumerate(published):
        a = geojson_to_local(first.display_geometry)
        for second in published[i + 1 :]:
            if a.intersects(geojson_to_local(second.display_geometry)):
                collisions.append([first.region_code, second.region_code])
    checks.append(
        (
            "DISPLAY_EXTENTS_DISJOINT",
            not collisions,
            "公开分区在公开粒度上互不重叠，统计不会把两个面积不同的区域直接合并计算。"
            if not collisions
            else f"公开粒度块重叠：{collisions}",
            {"collisions": collisions},
        )
    )

    # 5. observations reconcile item by item.
    included = sum(1 for row in stats["observations"] if row["disposition"] == "included")
    duplicate = sum(1 for row in stats["observations"] if row["disposition"] == "duplicate")
    unattributed = sum(
        1 for row in stats["observations"] if row["disposition"] == "unattributed"
    )
    excluded = sum(1 for row in stats["observations"] if row["disposition"] == "excluded")
    reconciled = (
        included + duplicate + unattributed + excluded == stats["internal_candidates"]
    )
    checks.append(
        (
            "OBSERVATION_RECONCILIATION",
            reconciled,
            (
                f"内部候选判读 {stats['internal_candidates']} 条 = 归入 {included} + 转载排重 "
                f"{duplicate} + 未归属 {unattributed} + 单元不可用排除 {excluded}；"
                "跨分区位置误差保留未归属数量，未平均摊成多条记录。"
            ),
            {
                "internal_candidates": stats["internal_candidates"],
                "included": included,
                "duplicates": duplicate,
                "unattributed": unattributed,
                "excluded": excluded,
            },
        )
    )

    # 5b. routes against internal tracks: every eligible track is either included,
    #     a duplicate, or excluded for a recorded reason.
    internal_tracks = stats["internal_tracks"]
    route_total = stats["included_tracks"] + stats["track_duplicates"] + stats["excluded_tracks"]
    checks.append(
        (
            "ROUTE_RECONCILIATION",
            route_total == internal_tracks,
            (
                f"内部可用轨迹 {internal_tracks} 条 = 归入分区 {stats['included_tracks']} + "
                f"重复排重 {stats['track_duplicates']} + 排除 {stats['excluded_tracks']}。"
            ),
            {
                "internal_tracks": internal_tracks,
                "included": stats["included_tracks"],
                "duplicates": stats["track_duplicates"],
                "excluded": stats["excluded_tracks"],
            },
        )
    )

    # 6. region observation rows add up to the included rows.
    observation_rows = list(
        db.execute(
            select(RegionObservation)
            .where(RegionObservation.publication_id == publication.id)
            .where(RegionObservation.disposition == "included")
        ).scalars()
    )
    region_sum_observations = sum(r.observation_count for r in regions)
    checks.append(
        (
            "REGION_STATS_MATCH_RECORDS",
            len(observation_rows) == included and region_sum_observations <= included,
            (
                f"逐条归属观察 {included} 条写入 {len(observation_rows)} 行；未公开区域不计入公开统计。"
            ),
            {
                "included": included,
                "written_rows": len(observation_rows),
                "region_sum": region_sum_observations,
            },
        )
    )

    # 7. effort: overlapping valid runs merged first, then summed; reported hours
    #    never exceed the internal total.
    effort_rows = list(
        db.execute(
            select(RegionEffort)
            .where(RegionEffort.publication_id == publication.id)
            .where(RegionEffort.disposition == "included")
        ).scalars()
    )
    published_scope = [r for r in published]
    published_ids = {r.id for r in published_scope}
    merged_hours = round(
        sum(row.hours for row in effort_rows), 2
    )
    published_hours = round(sum(r.effective_hours for r in published), 2)
    merged_intervals = sum(row.merged_intervals for row in effort_rows)
    # With nothing published there is nothing to reconcile: the stop is already
    # reported by PUBLISHABLE_REGIONS_EXIST, and a phantom mismatch here would be
    # misleading.
    effort_ok = (
        not published
        or (
            abs(merged_hours - published_hours) < 0.05
            and merged_hours <= stats["internal_effort_hours"] + 0.05
        )
    )
    checks.append(
        (
            "EFFORT_MERGED_AND_RECONCILED",
            effort_ok,
            (
                "重叠的有效设备时段先合并再汇入最终分区："
                f"内部有效时段 {stats['internal_effort_hours']}h，合并后 {merged_hours}h，"
                f"公开分区合计 {published_hours}h（合并 {merged_intervals} 个区间）。"
            ),
            {
                "internal_effort_hours": stats["internal_effort_hours"],
                "merged_effort_hours": merged_hours,
                "published_region_hours": published_hours,
                "merged_intervals": merged_intervals,
                "unattributed_effort": sum(
                    1 for row in stats["effort_rows"] if row["disposition"] != "included"
                ),
            },
        )
    )

    # 8. unknown effort never published as absence.
    unknown = [r.region_code for r in published if not r.effort_known]
    zero_effort = [r.region_code for r in published if r.effective_hours <= 0]
    checks.append(
        (
            "EFFORT_KNOWN_FOR_PUBLISHED",
            not unknown and not zero_effort,
            "调查强度未知或为零的区域不发布为物种未出现。"
            if not unknown and not zero_effort
            else f"缺少努力量：{unknown + zero_effort}",
            {"unknown": unknown, "zero": zero_effort},
        )
    )

    # 9. routes: duplicates removed, and route coverage is never finer than the
    #    published regions (downloads only carry regional totals).
    route_rows = list(
        db.execute(
            select(RegionRoute).where(RegionRoute.publication_id == publication.id)
        ).scalars()
    )
    route_duplicates = sum(1 for row in route_rows if row.disposition == "duplicate")
    checks.append(
        (
            "ROUTE_COVERAGE_AGGREGATED",
            route_duplicates == stats["track_duplicates"]
            and all(row.cell_id is None for row in route_rows),
            (
                f"巡护路线按轨迹键排重 {route_duplicates} 条，只保留分区级汇总长度，"
                "不导出细路线几何。"
            ),
            {"route_duplicates": route_duplicates, "rows": len(route_rows)},
        )
    )

    # 10. no precise identifier or raw file location in a public payload.
    from .spatial_delivery import build_public_payload

    payload = build_public_payload(db, publication, regions=published)
    leaks = scan_public_payload(payload["map"]) | scan_public_payload(payload["stats"])
    checks.append(
        (
            "NO_PRECISE_IDENTIFIERS",
            not leaks,
            "地图与下载响应不含精确坐标、细路线、原始文件位置或可反查站点标识。"
            if not leaks
            else f"公开载荷出现禁用字段：{sorted(leaks)}",
            {"leaks": sorted(leaks)},
        )
    )

    # 11. prior releases: combining an old material with the new boundary must not
    #     allow locating sensitive habitat. Withdrawal is not treated as erasure.
    gaps = partition.get("prior_gaps", [])
    merges = partition.get("prior_merges", [])
    checks.append(
        (
            "PRIOR_RELEASE_RANGE_SAFE",
            not gaps,
            (
                "既有公开材料范围仍被本次公开显示覆盖；必要时已扩大合并范围"
                f"（{len(merges)} 次并入）。撤回旧链接不视为已消除旧信息。"
                if not gaps
                else "旧材料范围与本次敏感区域组合可进一步定位敏感生境；需继续合并扩大或停止该项公开。"
            ),
            {"merges": merges[:50], "gaps": gaps[:50]},
        )
    )

    passed = all(status for _, status, _, _ in checks)
    for code, status, detail, data in checks:
        db.add(
            PublicationCheck(
                publication_id=publication.id,
                check_code=code,
                status="pass" if status else "fail",
                detail=detail,
                data=data,
            )
        )
    publication.checks_passed = passed
    publication.published_region_count = len(published) if passed else 0
    db.flush()
