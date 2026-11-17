"""Deterministic seed data covering the acceptance scenarios.

Scenario map (station → what it demonstrates):
  QSL-01  完整有效调查，含可验证校时关系与迟到送达记录
  QSL-02  相机帧连续缺失且停机日志未覆盖 → 设备可用性待核
  QSL-03  两来源互斥判读且均低于置信门槛 → 保留冲突
  YXG-01  轨迹仅擦过缓冲区边缘且定位精度大于缓冲区宽度 → 空间待核
  YXG-02  迟到判读修订改变目标物种出现 → 当前证书复审中、暂停趋势引用
  YXG-03  路线覆盖接近门槛 → 受限证书；概率估计仍支持 → 样本取舍分歧
  BHP-01  设备时间偏移超容差且无可验证校时 → 时间不满足季节窗口
  BHP-02  站点迁移后边界变化 → 按事件发生时的站点版本判断
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from .config import RESERVE_TZ
from .db import SessionLocal, init_db
from .models import (
    ArchiveRecord,
    Determination,
    ProbabilityModel,
    Rulebook,
    RulebookVersion,
    Station,
    StationVersion,
    SurveyRun,
    SurveyUnit,
    User,
    Zone,
)
from .security import hash_password
from .services import (
    build_run,
    create_trend_reference,
    ingest_record,
    revise_determination,
)
from .spatial import attribute_statistics, build_partition, create_publication
from .spatial_datasets import active_revision, seed_default_datasets
from .spatial_delivery import publish

SEASON = ("2026-04-01", "2026-04-30")
SURVEY_DATES = ["2026-04-10", "2026-04-11", "2026-04-12"]
LAT_M = 110_540.0
LON_M = 111_320.0


def latlon(lat0: float, lon0: float, dx: float, dy: float) -> tuple[float, float]:
    """Offset a point by ``dx``/``dy`` metres (local frame, degrees per metre)."""
    lon = lon0 + dx / (LON_M * math.cos(math.radians(lat0)))
    lat = lat0 + dy / LAT_M
    return lat, lon


def square_boundary(lat0: float, lon0: float, half: float) -> dict:
    ring_offsets = [
        (-half, -half),
        (half, -half),
        (half, half),
        (-half, half),
        (-half, -half),
    ]
    coords = []
    for dx, dy in ring_offsets:
        lat, lon = latlon(lat0, lon0, dx, dy)
        coords.append([round(lon, 7), round(lat, 7)])
    return {"type": "Polygon", "coordinates": [coords]}


def line_geojson(lat0: float, lon0: float, dx1: float, dy1: float, dx2: float, dy2: float) -> dict:
    lat1, lon1 = latlon(lat0, lon0, dx1, dy1)
    lat2, lon2 = latlon(lat0, lon0, dx2, dy2)
    return {
        "type": "LineString",
        "coordinates": [[round(lon1, 7), round(lat1, 7)], [round(lon2, 7), round(lat2, 7)]],
    }


def local_dt(day: str, hour: int, minute: int = 0) -> str:
    d = date.fromisoformat(day)
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=RESERVE_TZ).isoformat()


def utc_iso(day: str, hour: int, minute: int = 0, plus_hours: float = 0.0) -> str:
    d = date.fromisoformat(day)
    base = datetime(d.year, d.month, d.day, hour, minute, tzinfo=RESERVE_TZ)
    return (base + timedelta(hours=plus_hours)).astimezone(timezone.utc).isoformat()


STATIONS = [
    # code, name, zone, lat, lon, half-size m, buffer m, elev range, scenario
    ("QSL-01", "青松岭主监测站", "QS", 31.050049, 103.625805, 120, 30, (1850, 2050), "clean"),
    ("QSL-02", "青松岭北梁相机站", "QS", 31.050049, 103.613221, 120, 30, (1900, 2100), "frame_gap"),
    ("QSL-03", "青松岭西沟站", "QS", 31.060000, 103.630000, 120, 30, (1800, 1990), "conflict"),
    ("QSL-04", "青松岭东南站", "QS", 31.032861, 103.648876, 120, 30, (1750, 1930), "thin_reading"),
    ("QSL-05", "青松岭河岸辅站", "QS", 31.050049, 103.631049, 90, 30, (1710, 1900), "partial_stop"),
    ("YXG-01", "云溪沟西站", "YX", 30.978310, 103.537089, 120, 30, (1700, 1880), "edge"),
    ("YXG-02", "云溪沟东站", "YX", 30.986000, 103.550218, 120, 30, (1720, 1900), "late_revision"),
    ("YXG-03", "云溪沟下坝站", "YX", 30.993689, 103.563347, 120, 40, (1620, 1790), "partial_route"),
    ("BHP-01", "北坡高寒相机站", "BH", 31.119707, 103.700260, 110, 35, (2600, 2820), "clock_drift"),
    ("BHP-02", "北坡垭口迁移站", "BH", 31.134181, 103.718087, 100, 30, (2500, 2760), "migration"),
]

ZONES = [
    ("QS", "青松岭样区", "落叶阔叶林与针阔混交林过渡带，主监测样区。"),
    ("YX", "云溪沟样区", "沿沟谷布设的巡护路线样区，坡度较大。"),
    ("BH", "北坡高海拔样区", "高海拔针叶林与灌丛样区，冬季相机供电紧张。"),
]

TARGET_SPECIES = [
    {"code": "LOPHOPHORUS", "name": "绿尾虹雉", "min_detections": 3},
    {"code": "BUDORCAS", "name": "羚牛", "min_detections": 3},
    {"code": "AILUROPODA", "name": "大熊猫", "min_detections": 1},
]


def requirements() -> dict:
    return {
        "device": {
            "target_available_hours": 20,
            "min_available_hours": 12,
            "target_available_ratio": 0.83,
            "min_available_ratio": 0.5,
            "max_heartbeat_gap_hours": 6,
            "min_gap_hours": 1,
        },
        "time": {"tolerance_seconds": 120, "late_delivery_hours": 24},
        "space": {
            "target_route_coverage_ratio": 0.8,
            "min_route_coverage_ratio": 0.5,
            "edge_penetration_m": 5,
            "min_in_buffer_ratio": 0.5,
        },
        "reading": {
            "min_confidence": 0.7,
            "max_pending_ratio": 0.25,
            "min_available_observations": 3,
        },
        "schedule": {
            "survey_dates": SURVEY_DATES,
            "elevation_band_check": "required",
            "season_window_strict": True,
        },
    }


def route_segments(station_code: str, lat: float, lon: float, half: float) -> list[dict]:
    segments = [
        {
            "code": f"SEG-{station_code}-A",
            "name": f"{station_code} 主巡护路线",
            "geometry": line_geojson(lat, lon, -80, 0, 80, 0),
            "station_codes": [station_code],
            "elevation_band_m": [1600, 2200],
        }
    ]
    if station_code == "BHP-02":
        segments.append(
            {
                "code": "SEG-BHP-02-B",
                "name": "北坡垭口迁移后新增路线",
                "geometry": line_geojson(lat, lon, 120, 0, 280, 0),
                "station_codes": [station_code],
                "elevation_band_m": [2500, 2800],
                "valid_from": "2026-04-12",
            }
        )
        segments[0]["valid_to"] = "2026-04-11"
    return segments


def seed_users(db) -> None:
    users = [
        ("yq.li", "李延青", "researcher", "Survey#2026"),
        ("m.cao", "曹敏", "auditor", "Audit#2026"),
        ("l.lin", "林岚", "viewer", "View#2026"),
    ]
    for username, display, role, password in users:
        if db.execute(select(User).where(User.username == username)).scalar_one_or_none():
            continue
        db.add(
            User(
                username=username,
                display_name=display,
                role=role,
                password_hash=hash_password(password),
            )
        )
    db.flush()


def seed_catalog(db):
    zone_map = {}
    for code, name, desc in ZONES:
        zone = Zone(code=code, name=name, description=desc)
        db.add(zone)
        db.flush()
        zone_map[code] = zone

    station_map = {}
    for code, name, zone_code, lat, lon, half, buffer_m, elev, scenario in STATIONS:
        zone = zone_map[zone_code]
        station = Station(
            code=code,
            name=name,
            zone_id=zone.id,
            latitude=lat,
            longitude=lon,
            elevation_min_m=elev[0],
            elevation_max_m=elev[1],
            status="active",
        )
        db.add(station)
        db.flush()
        if scenario == "migration":
            v1 = StationVersion(
                station_id=station.id,
                version_number=1,
                boundary=square_boundary(lat, lon, half),
                buffer_width_m=buffer_m,
                centroid_lat=lat,
                centroid_lon=lon,
                elevation_min_m=elev[0],
                elevation_max_m=elev[1],
                effective_from=date(2026, 1, 1),
                effective_to=date(2026, 4, 11),
                change_reason="迁移前边界",
            )
            v2 = StationVersion(
                station_id=station.id,
                version_number=2,
                boundary=square_boundary(lat, lon, half * 1.7),
                buffer_width_m=buffer_m,
                centroid_lat=lat,
                centroid_lon=lon,
                elevation_min_m=elev[0],
                elevation_max_m=elev[1],
                effective_from=date(2026, 4, 12),
                effective_to=None,
                change_reason="2026-04-11 站点设备迁移至垭口东侧，边界向东扩展 70%",
            )
            db.add_all([v1, v2])
        else:
            db.add(
                StationVersion(
                    station_id=station.id,
                    version_number=1,
                    boundary=square_boundary(lat, lon, half),
                    buffer_width_m=buffer_m,
                    centroid_lat=lat,
                    centroid_lon=lon,
                    elevation_min_m=elev[0],
                    elevation_max_m=elev[1],
                    effective_from=date(2026, 1, 1),
                    effective_to=None,
                    change_reason="初始建站边界",
                )
            )
        db.flush()
        station_map[code] = station
    return zone_map, station_map


def seed_rulebook(db) -> RulebookVersion:
    master = Rulebook(
        code="RB-SPRING-2026",
        name="春季调查最低要求规则册",
        zone_id=None,
        description="覆盖设备可用、时间可信、空间经过与判读充分四类门槛；按站点—样区—日期形成调查单元。",
    )
    db.add(master)
    db.flush()
    segments = []
    for code, *_rest in STATIONS:
        lat, lon, half = _rest[2], _rest[3], _rest[4]
        segments.extend(route_segments(code, lat, lon, half))
    v1 = RulebookVersion(
        rulebook_id=master.id,
        version_number=1,
        label="2026 春季正式版",
        season_code="2026-SPRING",
        season_start=date(2026, 4, 1),
        season_end=date(2026, 4, 30),
        status="active",
        target_species=TARGET_SPECIES,
        requirements=requirements(),
        route_segments=segments,
        elevation_bands=[
            {"code": "LOW", "name": "低海拔沟谷", "min_m": 1500, "max_m": 1900},
            {"code": "MID", "name": "中海拔针阔混交", "min_m": 1900, "max_m": 2400},
            {"code": "HIGH", "name": "高海拔针叶灌丛", "min_m": 2400, "max_m": 3000},
        ],
        notes="正式未发现结论只使用本规则册签发的证书。",
    )
    draft_reqs = requirements()
    draft_reqs["reading"]["min_available_observations"] = 4
    draft_reqs["reading"]["max_pending_ratio"] = 0.15
    v2 = RulebookVersion(
        rulebook_id=master.id,
        version_number=2,
        label="2026 春季修订草案（未启用）",
        season_code="2026-SPRING",
        season_start=date(2026, 4, 1),
        season_end=date(2026, 4, 30),
        status="draft",
        target_species=TARGET_SPECIES,
        requirements=draft_reqs,
        route_segments=segments,
        elevation_bands=v1.elevation_bands,
        notes="草案提高判读门槛，待规则复核后启用。",
    )
    db.add_all([v1, v2])
    db.flush()
    return v1


def seed_model(db) -> ProbabilityModel:
    # Coverage score is a normalised weighted sum of coverage features (max ~0.85);
    # time trust and unresolved conflicts gate it to zero. Weights and the pivot were
    # calibrated so that route-shortfall units sit just above the 0.80 threshold while
    # thin-reading units sit just below it, which is what makes the rule/probability
    # comparison and the calibration-set sensitivity meaningful.
    model = ProbabilityModel(
        code="DET-LOGIT",
        name="检测概率 logistic 校准模型",
        version="1.3.0",
        status="active",
        threshold=0.80,
        calibration_sets=[
            {"label": "2019-2024 历史样本 (n=486)", "pivot_delta": 0.0, "n": 486},
            {"label": "2019-2023 历史样本 (n=402)", "pivot_delta": 0.04, "n": 402},
        ],
        parameters={
            "pivot": 0.62,
            "slope": 9.0,
            "weights": {
                "device_available_ratio": 0.14,
                "route_coverage_ratio": 0.13,
                "observation_count": 0.19,
                "pending_material_ratio": 0.17,
                "in_buffer_ratio": 0.14,
                "precision_ok": 0.08,
            },
            "gating_features": ["time_ok", "conflict_free"],
            "interval_z": 1.28,
            "interval_scale": 0.55,
        },
        threshold_review={
            "cadence": "每季复核",
            "last_review": "2026-03-25",
            "owner": "监测数据组",
            "next_review": "2026-07-01",
            "note": "阈值复核成本高，调整需同时登记校准集与影响样本数。",
        },
        notes="仅用于趋势研究的覆盖价值估算；不作为正式未发现结论依据。",
    )
    db.add(model)
    db.flush()
    return model


def _record(
    db,
    *,
    station,
    record_type,
    external_id,
    event_time,
    payload,
    device,
    received_time=None,
    lat=None,
    lon=None,
    precision=None,
    actor="seed",
):
    ingest_record(
        db,
        {
            "record_type": record_type,
            "external_id": external_id,
            "station_id": station.id,
            "event_time": event_time,
            "received_time": received_time or event_time,
            "source_device": device,
            "device_type": "camera" if record_type != "patrol_track" else "gps",
            "latitude": lat,
            "longitude": lon,
            "coord_precision_m": precision,
            "payload": payload,
        },
        actor,
    )


def _frame_latlon(station, code, dx, dy):
    return latlon(station.latitude, station.longitude, dx, dy)


def emit_device_day(db, station, scenario, day: str, station_version_lat=None, station_version_lon=None):
    lat0 = station_version_lat or station.latitude
    lon0 = station_version_lon or station.longitude
    device = f"CAM-{station.code}"
    # Heartbeats every two hours, with the reserve's normal power budget.
    for hour in range(0, 24, 2):
        _record(
            db,
            station=station,
            record_type="device_heartbeat",
            external_id=f"HB-{station.code}-{day.replace('-', '')}-{hour:02d}",
            event_time=utc_iso(day, hour),
            payload={
                "battery_pct": 78 - (hour % 6),
                "temperature_c": 4.5,
                "firmware": "3.2.1",
                "heartbeat_seq": hour // 2 + 1,
            },
            device=device,
        )

    # Frame sequence is the key device-health signal: a jump means missing frames.
    if scenario == "frame_gap":
        frame_plan = [(6, 1), (8, 2), (14, 9), (16, 10)]
    elif scenario == "thin_reading":
        frame_plan = [(6, 1), (8, 2), (14, 3), (16, 4)]
    else:
        frame_plan = [(6, 1), (8, 2), (10, 3), (12, 4), (14, 5), (16, 6)]

    frames = []
    # Frames sit inside the narrow riparian corridor (±55 m); the offset pattern is
    # wide enough to exercise thresholds but not to fall off the published habitat.
    for idx, (hour, seq) in enumerate(frame_plan):
        lat, lon = _frame_latlon(station, station.code, -40 + idx * 16, 6)
        payload = {
            "frame_seq": seq,
            "image_ref": f"s3://reserve-cam/{station.code}/{day}/IMG_{seq:04d}.jpg",
            "camera_id": device,
            "exposure_mode": "auto",
        }
        if scenario == "clock_drift":
            payload["clock_offset_seconds"] = 900
            payload["clock_note"] = "设备自检发现 RTC 偏移，未完成校时"
        _record(
            db,
            station=station,
            record_type="camera_event",
            external_id=f"IMG-{station.code}-{day.replace('-', '')}-{seq:04d}",
            event_time=utc_iso(day, hour),
            received_time=(
                utc_iso(day, hour, plus_hours=30)
                if scenario == "late_revision" and idx == 0
                else utc_iso(day, hour, 5)
            ),
            payload=payload,
            device=device,
            lat=lat,
            lon=lon,
            precision=8.0,
        )
        frames.append((payload["image_ref"], seq))

    # Documented downtime, except on the day with the unexplained frame gap.
    if scenario != "frame_gap":
        _record(
            db,
            station=station,
            record_type="downtime_log",
            external_id=f"DT-{station.code}-{day.replace('-', '')}",
            event_time=utc_iso(day, 10, 15),
            payload={
                "start": utc_iso(day, 10, 15),
                "end": utc_iso(day, 12, 15),
                "reason": "计划维护：更换电池与存储卡",
                "operator": "巡护一组",
                "camera_id": device,
            },
            device=device,
        )
    # 部分调查停机：只有一天出现计划外的长时间停机，可用时段降低但仍有记录。
    if scenario == "partial_stop" and day == "2026-04-11":
        _record(
            db,
            station=station,
            record_type="downtime_log",
            external_id=f"DT-{station.code}-{day.replace('-', '')}-PARTIAL",
            event_time=utc_iso(day, 18),
            payload={
                "start": utc_iso(day, 18),
                "end": utc_iso(day, 23, 30),
                "reason": "计划外停机：供电故障，设备自行恢复",
                "operator": "巡护二组",
                "camera_id": device,
            },
            device=device,
        )

    # Patrol track: how the route was actually walked.
    if scenario == "edge":
        geometry = line_geojson(lat0, lon0, -300, 149, 300, 149)
        precision = 40.0
        observer = "巡护三组"
    elif scenario == "partial_route":
        geometry = line_geojson(lat0, lon0, -48, 0, 48, 0)
        precision = 9.0
        observer = "巡护二组"
    elif scenario == "migration" and day == "2026-04-12":
        geometry = line_geojson(lat0, lon0, 120, 0, 280, 0)
        precision = 7.0
        observer = "巡护四组"
    else:
        geometry = line_geojson(lat0, lon0, -80, 0, 80, 0)
        precision = 9.0
        observer = "巡护一组"

    _record(
        db,
        station=station,
        record_type="patrol_track",
        external_id=f"TRK-{station.code}-{day.replace('-', '')}",
        event_time=utc_iso(day, 7),
        received_time=utc_iso(day, 19),
        payload={
            "geometry": geometry,
            "observer": observer,
            "route_code": f"SEG-{station.code}-A",
            "distance_km": 6.4,
            "elevation_gain_m": 320,
        },
        device=f"GPS-{station.code}",
        precision=precision,
    )
    return frames


def emit_determinations(db, station, scenario, day: str, frames) -> None:
    from .services import add_determination

    for idx, (image_ref, seq) in enumerate(frames):
        material = db.execute(
            select(ArchiveRecord).where(
                ArchiveRecord.external_id == f"IMG-{station.code}-{day.replace('-', '')}-{seq:04d}"
            )
        ).scalar_one()
        species = TARGET_SPECIES[idx % len(TARGET_SPECIES)]["code"]
        if scenario == "thin_reading" and idx < 2:
            add_determination(
                db,
                material_record_id=material.id,
                source="analyst",
                species_code=species,
                verdict="uncertain",
                confidence=0.44,
                analyst="周冕",
                notes="光线不足，仅能确认为空帧，未达置信门槛",
                actor="seed",
            )
            continue
        if scenario == "conflict" and day == "2026-04-11" and idx == 2:
            add_determination(
                db,
                material_record_id=material.id,
                source="analyst",
                species_code=species,
                verdict="present",
                confidence=0.58,
                analyst="周冕",
                notes="形态疑似，未见清晰体侧斑纹",
                actor="seed",
            )
            add_determination(
                db,
                material_record_id=material.id,
                source="partner",
                species_code=species,
                verdict="absent",
                confidence=0.49,
                analyst="合作机构判读模型",
                notes="无相似目标特征",
                actor="seed",
            )
            continue
        if scenario == "late_revision" and day == "2026-04-11" and idx == 1:
            add_determination(
                db,
                material_record_id=material.id,
                source="analyst",
                species_code=species,
                verdict="absent",
                confidence=0.82,
                analyst="周冕",
                notes="初判未见目标物种",
                actor="seed",
            )
            continue
        add_determination(
            db,
            material_record_id=material.id,
            source="analyst",
            species_code=species,
            verdict="absent",
            confidence=round(0.78 + 0.03 * idx, 2),
            analyst="周冕",
            notes="逐帧判读，无可确认目标特征",
            actor="seed",
        )


def seed_archive(db, zone_map, station_map) -> None:
    for code, name, zone_code, lat, lon, half, buffer_m, elev, scenario in STATIONS:
        station = station_map[code]
        metadata_payload = {
            "station_code": code,
            "zone_code": zone_code,
            "elevation_band": "MID" if elev[1] < 2400 else "HIGH",
            "buffer_width_m": buffer_m,
            "access": "徒步 3.2km",
        }
        version = db.execute(
            select(StationVersion)
            .where(StationVersion.station_id == station.id)
            .order_by(StationVersion.version_number)
        ).scalars().first()
        if version is not None:
            metadata_payload["station_version"] = version.version_number
        ingest_record(
            db,
            {
                "record_type": "station_metadata",
                "external_id": f"META-{code}-V1",
                "station_id": station.id,
                "event_time": datetime(2026, 3, 28, 16, 0, tzinfo=timezone.utc).isoformat(),
                "received_time": datetime(2026, 3, 28, 16, 5, tzinfo=timezone.utc).isoformat(),
                "source_device": "reserve-gis",
                "device_type": "metadata",
                "payload": metadata_payload,
            },
            "seed",
        )
        if scenario == "migration":
            second_payload = dict(metadata_payload)
            second_payload["station_version"] = 2
            second_payload["note"] = "迁移后站点边界更新，事件时间决定适用版本"
            ingest_record(
                db,
                {
                    "record_type": "station_metadata",
                    "external_id": f"META-{code}-V2",
                    "station_id": station.id,
                    "event_time": datetime(2026, 4, 12, 3, 0, tzinfo=timezone.utc).isoformat(),
                    "received_time": datetime(2026, 4, 12, 3, 6, tzinfo=timezone.utc).isoformat(),
                    "source_device": "reserve-gis",
                    "device_type": "metadata",
                    "payload": second_payload,
                },
                "seed",
            )
        if scenario == "clean":
            _record(
                db,
                station=station,
                record_type="time_calibration",
                external_id=f"CAL-{code}-01",
                event_time=utc_iso("2026-04-09", 9),
                payload={
                    "target_device": f"CAM-{code}",
                    "offset_seconds": 300,
                    "verified": True,
                    "valid_from": utc_iso("2026-04-09", 9),
                    "valid_to": utc_iso("2026-05-01", 9),
                    "source": "保护区 NTP 参考站校时单 CT-2026-041",
                    "method": "现场对时，回算 300s 偏移",
                },
                device="reserve-timesync",
            )
        for day in SURVEY_DATES:
            frames = emit_device_day(db, station, scenario, day)
            emit_determinations(db, station, scenario, day, frames)
        db.flush()


def seed_run(db, rulebook: RulebookVersion, model: ProbabilityModel):
    run = build_run(
        db,
        rulebook_version=rulebook,
        model=model,
        station_ids=None,
        label="2026 春季调查覆盖组装",
        actor="yq.li",
    )
    db.flush()
    return run


def _unit(db, run, station_code: str, day: str) -> SurveyUnit:
    station = db.execute(select(Station).where(Station.code == station_code)).scalar_one()
    return db.execute(
        select(SurveyUnit)
        .where(SurveyUnit.run_id == run.id)
        .where(SurveyUnit.station_id == station.id)
        .where(SurveyUnit.unit_date == date.fromisoformat(day))
    ).scalar_one()


def seed_references_and_revision(db, run: SurveyRun) -> None:
    create_trend_reference(
        db,
        unit_id=_unit(db, run, "QSL-01", "2026-04-10").id,
        purpose="absence",
        actor="yq.li",
        note="青松岭主站 D1 规则证书完整，用于正式未发现结论引用。",
    )
    for day in SURVEY_DATES:
        create_trend_reference(
            db,
            unit_id=_unit(db, run, "YXG-03", day).id,
            purpose="trend",
            actor="yq.li",
            note="路线覆盖接近门槛，规则受限但概率估计支持，作为趋势参考样本。",
        )
    create_trend_reference(
        db,
        unit_id=_unit(db, run, "BHP-02", "2026-04-12").id,
        purpose="trend",
        actor="yq.li",
        note="迁移后新边界单元，设备与空间均满足。",
    )
    late_unit = _unit(db, run, "YXG-02", "2026-04-11")
    create_trend_reference(
        db,
        unit_id=late_unit.id,
        purpose="trend",
        actor="yq.li",
        note="云溪沟东站 D2 早期趋势引用，待迟到判读复核。",
    )

    # Late determination revision: absent → present, which invalidates the
    # earlier certificate's absence use and suspends the trend reference.
    material = db.execute(
        select(ArchiveRecord).where(ArchiveRecord.external_id == "IMG-YXG-02-20260411-0002")
    ).scalar_one()
    det = db.execute(
        select(Determination)
        .where(Determination.material_record_id == material.id)
        .order_by(Determination.id)
    ).scalars().first()
    revise_determination(
        db,
        det.id,
        verdict="present",
        confidence=0.86,
        reason="迟到判读复核：重看原片后确认目标物种出现在画面右侧，初判遗漏。",
        actor="m.cao",
    )


def seed_autumn_rulebook(db) -> RulebookVersion:
    master = db.execute(select(Rulebook).where(Rulebook.code == "RB-SPRING-2026")).scalar_one()
    existing = db.execute(
        select(RulebookVersion)
        .where(RulebookVersion.rulebook_id == master.id)
        .where(RulebookVersion.season_code == "2026-AUTUMN")
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    base = db.execute(
        select(RulebookVersion)
        .where(RulebookVersion.rulebook_id == master.id)
        .where(RulebookVersion.version_number == 1)
    ).scalar_one()
    autumn = RulebookVersion(
        rulebook_id=master.id,
        version_number=4,
        label="2026 秋季正式版",
        season_code="2026-AUTUMN",
        season_start=date(2026, 9, 1),
        season_end=date(2026, 9, 30),
        status="active",
        target_species=base.target_species,
        requirements={**base.requirements, "schedule": {"survey_dates": ["2026-09-10", "2026-09-11", "2026-09-12"]}},
        route_segments=base.route_segments,
        elevation_bands=base.elevation_bands,
        notes="秋季调查沿用春季门槛，用于与春季结果做共同范围比较。",
    )
    db.add(autumn)
    db.flush()
    return autumn


AUTUMN_DAYS = ("2026-09-10", "2026-09-11", "2026-09-12")


def _autumn_record(db, station, record_type, external_id, event_time, payload, device,
                   received_time=None, lat=None, lon=None, precision=None):
    _record(
        db,
        station=station,
        record_type=record_type,
        external_id=external_id,
        event_time=event_time,
        payload=payload,
        device=device,
        received_time=received_time,
        lat=lat,
        lon=lon,
        precision=precision,
        actor="seed",
    )


def seed_autumn_archive(db, station_map, species: str) -> None:
    """秋季数据：观察、转载素材、跨分区定位误差与部分调查停机。

    只处理青松岭主站与河岸辅站（目标物种绿尾虹雉），方便与春季结果按共同范围比较。
    """
    for code in ("QSL-01", "QSL-05"):
        station = station_map[code]
        device = f"CAM-{code}"
        for day in AUTUMN_DAYS:
            for hour in range(0, 24, 2):
                _autumn_record(
                    db,
                    station,
                    "device_heartbeat",
                    f"AUT-HB-{code}-{day.replace('-', '')}-{hour:02d}",
                    utc_iso(day, hour),
                    {
                        "battery_pct": 74 - (hour % 5),
                        "temperature_c": 9.5,
                        "firmware": "3.2.1",
                        "heartbeat_seq": hour // 2 + 1,
                        "season": "autumn",
                    },
                    device,
                )
            for idx, (hour, seq) in enumerate(
                [(6, 1), (8, 2), (10, 3), (12, 4), (14, 5), (16, 6)]
            ):
                lat, lon = _frame_latlon(station, code, -40 + idx * 16, 6)
                _autumn_record(
                    db,
                    station,
                    "camera_event",
                    f"AUT-IMG-{code}-{day.replace('-', '')}-{seq:04d}",
                    utc_iso(day, hour),
                    {
                        "frame_seq": seq,
                        "image_ref": f"s3://reserve-cam/{code}/{day}/AUT_{seq:04d}.jpg",
                        "camera_id": device,
                        "material_key": f"AUT-{code}-{day}-{seq:04d}",
                        "season": "autumn",
                    },
                    device,
                    received_time=utc_iso(day, hour, 3),
                    lat=lat,
                    lon=lon,
                    precision=8.0,
                )
            # 部分调查停机：河岸辅站秋季只有一天有较长的计划外停机。
            if code == "QSL-05" and day == AUTUMN_DAYS[1]:
                _autumn_record(
                    db,
                    station,
                    "downtime_log",
                    f"AUT-DT-{code}-{day.replace('-', '')}",
                    utc_iso(day, 17),
                    {
                        "start": utc_iso(day, 17),
                        "end": utc_iso(day, 22),
                        "reason": "秋季计划外停机：太阳能板遮挡",
                        "camera_id": device,
                    },
                    device,
                )
            _autumn_record(
                db,
                station,
                "patrol_track",
                f"AUT-TRK-{code}-{day.replace('-', '')}",
                utc_iso(day, 7),
                {
                    "geometry": line_geojson(station.latitude, station.longitude, -70, 0, 70, 0),
                    "observer": "巡护一组",
                    "route_code": f"SEG-{code}-A",
                    "track_key": f"AUT-TRK-{code}-{day}",
                },
                f"GPS-{code}",
                received_time=utc_iso(day, 18),
                precision=9.0,
            )

    # 跨分区定位误差：位置落在溪岸带与混交林发布分区交界，误差半径跨过边界。
    station = station_map["QSL-05"]
    # 位于溪岸带（区 2）与东段河岸带（区 3）之间的窄缝上，误差半径同时触及两块分区。
    lat, lon = _frame_latlon(station, "QSL-05", -300, 0)
    _autumn_record(
        db,
        station,
        "camera_event",
        "AUT-IMG-BOUNDARY-0001",
        utc_iso(AUTUMN_DAYS[0], 9),
        {
            "frame_seq": 7,
            "image_ref": "s3://reserve-cam/QSL-05/cross.png",
            "camera_id": "CAM-QSL-05",
            "material_key": "AUT-CROSS-0001",
            "season": "autumn",
            "location_note": "定位误差覆盖两个发布分区",
        },
        "CAM-QSL-05",
        received_time=utc_iso(AUTUMN_DAYS[0], 12),
        lat=lat,
        lon=lon,
        precision=350.0,
    )
    # 转载素材：同一图片从合作机构渠道再次提交，应排重只计一次。
    original = db.execute(
        select(ArchiveRecord).where(
            ArchiveRecord.external_id
            == f"AUT-IMG-QSL-01-{AUTUMN_DAYS[0].replace('-', '')}-0002"
        )
    ).scalar_one()
    _autumn_record(
        db,
        station_map["QSL-01"],
        "camera_event",
        "AUT-IMG-QSL-01-0002-REPOST",
        utc_iso(AUTUMN_DAYS[0], 8, 30),
        {
            "frame_seq": 2,
            "image_ref": "partner-mirror://qsl/AUT_0002.jpg",
            "camera_id": "CAM-QSL-01",
            "material_key": f"AUT-QSL-01-{AUTUMN_DAYS[0]}-0002",
            "repost_of": original.external_id,
            "season": "autumn",
        },
        "partner-portal",
        received_time=utc_iso(AUTUMN_DAYS[0], 20),
        lat=original.latitude,
        lon=original.longitude,
        precision=8.0,
    )


def seed_autumn_determinations(db, species: str) -> None:
    from .services import add_determination

    present_keys = {
        "AUT-IMG-QSL-01-20260910-0003",
        "AUT-IMG-QSL-05-20260911-0004",
    }
    for record in db.execute(
        select(ArchiveRecord)
        .where(ArchiveRecord.external_id.like("AUT-IMG-%"))
        .where(ArchiveRecord.external_id != "AUT-IMG-BOUNDARY-0001")
        .order_by(ArchiveRecord.id)
    ).scalars():
        verdict = "present" if record.external_id in present_keys else "absent"
        add_determination(
            db,
            material_record_id=record.id,
            source="analyst",
            species_code=species,
            verdict=verdict,
            confidence=0.88 if verdict == "present" else 0.81,
            analyst="周冕",
            notes="秋季逐帧判读",
            actor="seed",
        )
    boundary = db.execute(
        select(ArchiveRecord).where(ArchiveRecord.external_id == "AUT-IMG-BOUNDARY-0001")
    ).scalar_one()
    add_determination(
        db,
        material_record_id=boundary.id,
        source="analyst",
        species_code=species,
        verdict="absent",
        confidence=0.84,
        analyst="周冕",
        notes="秋季跨界位置素材，定位误差覆盖两个发布分区",
        actor="seed",
    )


def seed_autumn(db, station_map, model: ProbabilityModel):
    """Build the autumn run and reproduce the demo publication cycle.

    1. spring publication for 绿尾虹雉 under sensitivity v1;
    2. autumn publication under v1;
    3. raise sensitivity to critical and revise the habitat (v2);
    4. rebuild affected partitions and re-check.
    """
    autumn_rulebook = seed_autumn_rulebook(db)
    species = "LOPHOPHORUS"
    seed_autumn_archive(db, station_map, species)
    seed_autumn_determinations(db, species)
    db.flush()
    run = build_run(
        db,
        rulebook_version=autumn_rulebook,
        model=model,
        station_ids=None,
        label="2026 秋季调查覆盖组装",
        actor="yq.li",
    )
    db.flush()
    return run


def seed_spring_publication(db, spring_run: SurveyRun) -> None:
    seed_publication(db, spring_run, season_label="春季")


def seed_publication(db, run: SurveyRun, *, season_label: str) -> None:
    """Create and publish a baseline delivery so the map has content on first boot.

    The sensitivity escalation + habitat revision + re-check cycle is reproduced by
    ``backend/scripts/spatial_demo.py`` (and covered by the automated tests), so a
    normal boot stays fast.
    """
    publication = create_publication(
        db,
        species_code="LOPHOPHORUS",
        scope_code="PUBLIC",
        season_code=run.season_code,
        run=run,
        actor="yq.li",
        note=f"{season_label}发布（敏感级别 high，生境 v1）",
    )
    if publication.checks_passed:
        publish(db, publication, "yq.li", f"{season_label}公众分布地图与数据文件")


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        existing = db.execute(select(SurveyRun)).scalars().first()
        if existing:
            print("seed: data already present, skipping")
            return
        seed_users(db)
        zone_map, station_map = seed_catalog(db)
        rulebook = seed_rulebook(db)
        model = seed_model(db)
        seed_default_datasets(db)
        seed_archive(db, zone_map, station_map)
        run = seed_run(db, rulebook, model)
        seed_references_and_revision(db, run)
        autumn_run = seed_autumn(db, station_map, model)
        db.flush()
        seed_spring_publication(db, run)
        seed_publication(db, autumn_run, season_label="秋季")
        db.commit()
        print(f"seed: run {run.run_code} with {len(run.units)} survey units")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
