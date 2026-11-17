from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from shapely import transform as shp_transform
from shapely.geometry import LineString, Point, shape

from .config import RESERVE_TZ
from .models import StationVersion

_LAT_M = 110_540.0
_LON_M_PER_DEG = 111_320.0


def canonical_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        # Naive timestamps are interpreted as reserve local time (UTC+8).
        return dt.replace(tzinfo=RESERVE_TZ).astimezone(timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return ensure_utc(value)
    return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def local_date(dt: datetime) -> date:
    return ensure_utc(dt).astimezone(RESERVE_TZ).date()


def day_window(unit_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(unit_date, time.min, tzinfo=RESERVE_TZ)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def season_window(start: date, end: date) -> tuple[datetime, datetime]:
    start_dt = datetime.combine(start, time.min, tzinfo=RESERVE_TZ)
    end_dt = datetime.combine(end + timedelta(days=1), time.min, tzinfo=RESERVE_TZ)
    return start_dt.astimezone(timezone.utc), end_dt.astimezone(timezone.utc)


def resolve_station_version(
    versions: list[StationVersion], at: datetime
) -> StationVersion | None:
    target = local_date(at)
    candidates = [
        v
        for v in versions
        if v.effective_from <= target and (v.effective_to is None or target <= v.effective_to)
    ]
    if not candidates:
        ordered = sorted(versions, key=lambda v: v.effective_from)
        if ordered and target < ordered[0].effective_from:
            return ordered[0]
        return ordered[-1] if ordered else None
    return max(candidates, key=lambda v: v.effective_from)


def _to_local_factory(lat0: float, lon0: float):
    """Build a projection to a local metre frame anchored on (lat0, lon0)."""
    lon_scale = _LON_M_PER_DEG * math.cos(math.radians(lat0))

    def _fn(coords):
        out = coords.copy()
        out[:, 0] = (coords[:, 0] - lon0) * lon_scale
        out[:, 1] = (coords[:, 1] - lat0) * _LAT_M
        return out

    return _fn


def _from_local_factory(lat0: float, lon0: float):
    lon_scale = _LON_M_PER_DEG * math.cos(math.radians(lat0))

    def _fn(coords):
        out = coords.copy()
        out[:, 0] = lon0 + coords[:, 0] / lon_scale
        out[:, 1] = lat0 + coords[:, 1] / _LAT_M
        return out

    return _fn


def to_local(geom, lat0: float, lon0: float):
    return shp_transform(geom, _to_local_factory(lat0, lon0))


def from_local(geom, lat0: float, lon0: float):
    return shp_transform(geom, _from_local_factory(lat0, lon0))


def station_buffer(version: StationVersion):
    """Station boundary expanded by its required survey buffer, in metres."""
    boundary = shape(version.boundary)
    local = to_local(boundary, version.centroid_lat, version.centroid_lon)
    buffered = local.buffer(version.buffer_width_m)
    return from_local(buffered, version.centroid_lat, version.centroid_lon)


def merge_intervals(
    intervals: list[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    ordered = sorted((s, e) for s, e in intervals if e > s)
    merged: list[tuple[datetime, datetime]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def hours_between(start: datetime, end: datetime) -> float:
    return max(0.0, (end - start).total_seconds() / 3600)


def track_geometry(payload: dict[str, Any]) -> LineString | None:
    geom = payload.get("geometry")
    if not geom:
        return None
    try:
        parsed = shape(geom)
    except Exception:
        return None
    if parsed.geom_type != "LineString" or parsed.is_empty:
        return None
    return parsed


def analyze_track(geom: LineString, version: StationVersion) -> dict[str, Any]:
    """Measure how a patrol track relates to the station buffer, in metres."""
    lat0, lon0 = version.centroid_lat, version.centroid_lon
    local_track = to_local(geom, lat0, lon0)
    local_buffer = to_local(station_buffer(version), lat0, lon0)
    track_length = local_track.length
    intersection = local_track.intersection(local_buffer)
    inside_length = intersection.length if not intersection.is_empty else 0.0
    boundary = local_buffer.boundary
    penetration = 0.0
    if inside_length > 0:
        parts = []
        if intersection.geom_type == "LineString":
            parts = [intersection]
        elif intersection.geom_type == "MultiLineString":
            parts = list(intersection.geoms)
        points = [Point(c) for part in parts for c in part.coords]
        if points:
            penetration = max(p.distance(boundary) for p in points)
    distance_outside = 0.0 if inside_length > 0 else local_track.distance(local_buffer)
    coverage_ratio = min(1.0, inside_length / track_length) if track_length else 0.0
    return {
        "track_length_m": round(track_length, 1),
        "inside_length_m": round(inside_length, 1),
        "distance_to_buffer_m": round(distance_outside, 1),
        "penetration_depth_m": round(penetration, 1),
        "coverage_ratio": round(coverage_ratio, 3),
        "buffer_width_m": version.buffer_width_m,
    }


def segment_coverage(geom: LineString, version: StationVersion, segment: dict[str, Any]) -> float:
    """Fraction of a rulebook route segment inside the station buffer that a track covers."""
    seg_geom = shape(segment["geometry"])
    lat0, lon0 = version.centroid_lat, version.centroid_lon
    local_seg = to_local(seg_geom, lat0, lon0)
    local_buffer = to_local(station_buffer(version), lat0, lon0)
    target = local_seg.intersection(local_buffer)
    if target.is_empty or target.length == 0:
        return 0.0
    covered = to_local(geom, lat0, lon0).intersection(target.buffer(15.0))
    return round(min(1.0, covered.length / target.length), 3)


def polygon_point_match(version: StationVersion, lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return False
    return station_buffer(version).covers(Point(lon, lat))


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None
