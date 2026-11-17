"""Coverage assembler: turns raw archive records into station-zone-date survey units.

The assembler is deliberately evidence-first: every measured value keeps the record
ids it came from, and every gate keeps the rulebook clause that set its thresholds.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import (
    GATE_LABELS,
    STATUS_INVALID,
    STATUS_PENDING,
    STATUS_RESTRICTED,
    STATUS_VALID,
)
from .models import (
    ArchiveRecord,
    Determination,
    GateCheck,
    RulebookVersion,
    Station,
    StationVersion,
    SurveyUnit,
)
from .utils import (
    analyze_track,
    day_window,
    ensure_utc,
    hours_between,
    local_date,
    merge_intervals,
    resolve_station_version,
    segment_coverage,
    season_window,
    track_geometry,
)

GATE_ORDER = ("device", "time", "space", "reading")


@dataclass
class TimedRecord:
    record: ArchiveRecord
    effective_time: datetime
    corrected: bool
    calibration_ids: list[int] = field(default_factory=list)
    time_issue: str | None = None
    late: bool = False


@dataclass
class StationContext:
    station: Station
    versions: list[StationVersion]
    records: list[TimedRecord] = field(default_factory=list)
    by_date: dict[date, list[TimedRecord]] = field(default_factory=dict)
    determinations: dict[int, list[Determination]] = field(default_factory=dict)
    determinations_by_material: dict[int, list[Determination]] = field(default_factory=dict)


def _finding(
    gate: str,
    requirement_code: str,
    status: str,
    measured: dict[str, Any],
    threshold: dict[str, Any],
    explanation: str,
    rule_ref: str,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "gate": gate,
        "gate_label": GATE_LABELS.get(gate, gate),
        "requirement_code": requirement_code,
        "status": status,
        "measured": measured,
        "threshold": threshold,
        "explanation": explanation,
        "rule_ref": rule_ref,
        "evidence": evidence,
    }


def _union_hours(intervals: list[tuple[datetime, datetime]]) -> float:
    return sum(hours_between(s, e) for s, e in merge_intervals(intervals))


def _clip(intervals: list[tuple[datetime, datetime]], start, end):
    clipped = []
    for s, e in intervals:
        s2, e2 = max(s, start), min(e, end)
        if e2 > s2:
            clipped.append((s2, e2))
    return clipped


def _covered(intervals: list[tuple[datetime, datetime]], start, end) -> bool:
    return any(s <= start and e >= end for s, e in merge_intervals(intervals))


class CoverageAssembler:
    def __init__(
        self,
        db: Session,
        rulebook: RulebookVersion,
        stations: list[Station],
        model_id: int | None = None,
    ) -> None:
        self.db = db
        self.rulebook = rulebook
        self.stations = stations
        self.model_id = model_id
        self.req = rulebook.requirements
        self.season_start_dt, self.season_end_dt = season_window(
            rulebook.season_start, rulebook.season_end
        )
        self.contexts: dict[int, StationContext] = {}
        self._load()

    # ------------------------------------------------------------------ loading
    def _load(self) -> None:
        station_ids = [s.id for s in self.stations]
        versions = (
            self.db.execute(
                select(StationVersion).where(StationVersion.station_id.in_(station_ids))
            )
            .scalars()
            .all()
        )
        by_station: dict[int, list[StationVersion]] = defaultdict(list)
        for v in versions:
            by_station[v.station_id].append(v)

        records = (
            self.db.execute(
                select(ArchiveRecord)
                .where(ArchiveRecord.station_id.in_(station_ids))
                .where(ArchiveRecord.event_time >= self.season_start_dt - timedelta(days=2))
                .where(ArchiveRecord.event_time < self.season_end_dt + timedelta(days=2))
                .order_by(ArchiveRecord.event_time)
            )
            .scalars()
            .all()
        )
        calib_by_station: dict[int, list[ArchiveRecord]] = defaultdict(list)
        for rec in records:
            if rec.record_type == "time_calibration":
                calib_by_station[rec.station_id].append(rec)

        dets = (
            self.db.execute(
                select(Determination)
                .where(Determination.status == "active")
                .order_by(Determination.id)
            )
            .scalars()
            .all()
        )
        dets_by_material: dict[int, list[Determination]] = defaultdict(list)
        for d in dets:
            dets_by_material[d.material_record_id].append(d)

        for station in self.stations:
            ctx = StationContext(
                station=station,
                versions=sorted(by_station.get(station.id, []), key=lambda v: v.effective_from),
                determinations_by_material=dets_by_material,
            )
            calibrations = calib_by_station.get(station.id, [])
            for rec in records:
                if rec.station_id != station.id:
                    continue
                if rec.record_type in {"time_calibration", "station_metadata"}:
                    effective = ensure_utc(rec.event_time)
                    timed = TimedRecord(rec, effective, False, [rec.id])
                    ctx.records.append(timed)
                    continue
                timed = self._apply_calibration(rec, calibrations)
                timed.late = (
                    ensure_utc(rec.received_time) - timed.effective_time
                ) > timedelta(hours=self.req["time"]["late_delivery_hours"])
                ctx.records.append(timed)

            for timed_rec in ctx.records:
                rec = timed_rec.record
                if rec.record_type in {"time_calibration", "station_metadata"}:
                    continue
                ctx.by_date.setdefault(local_date(timed_rec.effective_time), []).append(timed_rec)
            self.contexts[station.id] = ctx

    def _apply_calibration(
        self, rec: ArchiveRecord, calibrations: list[ArchiveRecord]
    ) -> TimedRecord:
        event_time = ensure_utc(rec.event_time)
        tolerance = self.req["time"]["tolerance_seconds"]
        applicable = []
        for cal in calibrations:
            payload = cal.payload
            if payload.get("target_device") and payload["target_device"] != rec.source_device:
                continue
            valid_from = payload.get("valid_from")
            valid_to = payload.get("valid_to")
            if valid_from and event_time < ensure_utc(datetime.fromisoformat(valid_from)):
                continue
            if valid_to and event_time > ensure_utc(datetime.fromisoformat(valid_to)):
                continue
            if not payload.get("verified", False):
                continue
            applicable.append((cal, abs(float(payload.get("offset_seconds", 0.0)))))
        if applicable:
            cal, offset = max(applicable, key=lambda pair: pair[1])
            corrected = event_time - timedelta(seconds=float(cal.payload["offset_seconds"]))
            return TimedRecord(rec, corrected, True, [cal.id])

        declared = rec.payload.get("clock_offset_seconds")
        if declared is not None and abs(float(declared)) > tolerance:
            return TimedRecord(
                rec,
                event_time,
                False,
                [],
                time_issue=f"设备自检时钟偏移 {float(declared):.0f}s 超过容差 {tolerance}s，且无已验证校时关系",
            )
        return TimedRecord(rec, event_time, False, [])

    # -------------------------------------------------------------- unit build
    def season_dates(self) -> list[date]:
        dates = [date.fromisoformat(d) for d in self.req["schedule"]["survey_dates"]]
        return [d for d in sorted(dates) if self.rulebook.season_start <= d <= self.rulebook.season_end]

    def route_segments_for(self, station: Station, unit_date: date | None = None) -> list[dict[str, Any]]:
        segments = [
            seg
            for seg in self.rulebook.route_segments
            if not seg.get("station_codes") or station.code in seg["station_codes"]
        ]
        if unit_date is not None:
            segments = [
                seg
                for seg in segments
                if (not seg.get("valid_from") or date.fromisoformat(seg["valid_from"]) <= unit_date)
                and (not seg.get("valid_to") or unit_date <= date.fromisoformat(seg["valid_to"]))
            ]
        return segments

    def build_unit_features(self, station: Station, unit_date: date) -> dict[str, Any]:
        ctx = self.contexts[station.id]
        version = resolve_station_version(ctx.versions, datetime.combine(unit_date, datetime.min.time()))
        if version is None:
            raise ValueError(f"站点 {station.code} 在 {unit_date} 无可用站点版本")

        start, end = day_window(unit_date)
        all_recs = ctx.by_date.get(unit_date, [])
        recs = [r for r in all_recs if start <= r.effective_time < end]

        frames = [
            r
            for r in recs
            if r.record.record_type == "camera_event"
        ]
        heartbeats = [r for r in recs if r.record.record_type == "device_heartbeat"]
        downtimes = [r for r in recs if r.record.record_type == "downtime_log"]
        tracks = [r for r in recs if r.record.record_type == "patrol_track"]

        downtime_intervals = []
        for d in downtimes:
            payload = d.record.payload
            ds = ensure_utc(datetime.fromisoformat(payload["start"]))
            de = ensure_utc(datetime.fromisoformat(payload["end"]))
            downtime_intervals.append((ds, de))

        device = self._device_gate(
            unit_date, start, end, frames, heartbeats, downtime_intervals, downtimes
        )
        time_gate = self._time_gate(recs, device)
        space = self._space_gate(recs, frames, tracks, version, station, unit_date)
        reading = self._reading_gate(recs, frames, ctx)

        gates = [device, time_gate, space, reading]
        statuses = [g["status"] for g in gates]
        if "pending" in statuses:
            status = STATUS_PENDING
        elif "fail" in statuses:
            status = STATUS_INVALID
        elif "restricted" in statuses:
            status = STATUS_RESTRICTED
        else:
            status = STATUS_VALID

        open_conflicts = reading["measured"].get("open_conflicts", 0)
        absence_eligible = status == STATUS_VALID and open_conflicts == 0
        trend_eligible = status in {STATUS_VALID, STATUS_RESTRICTED} and "pending" not in statuses

        issues = self._collect_issues(gates, frames, tracks, recs, ctx)
        feature_vector = {
            "device_available_ratio": device["measured"]["available_ratio"],
            "device_available_hours": device["measured"]["available_hours"],
            "route_coverage_ratio": space["measured"]["route_coverage_ratio"],
            "route_inside_length_m": space["measured"]["inside_length_m"],
            "observation_count": reading["measured"]["target_absent"] + reading["measured"]["target_present"],
            "pending_material_ratio": reading["measured"]["pending_ratio"],
            "in_buffer_ratio": space["measured"]["in_buffer_ratio"],
            "precision_ok": 1.0 if space["measured"]["precision_ok"] else 0.0,
            "time_ok": 1.0 if time_gate["status"] == "pass" else 0.0,
            "conflict_free": 1.0 if open_conflicts == 0 else 0.0,
            "gate_statuses": {g["gate"]: g["status"] for g in gates},
        }
        summary = {
            "target_species": [t["code"] for t in self.rulebook.target_species],
            "material_count": len(frames),
            "determination_count": reading["measured"]["determinations_total"],
            "pending_materials": reading["measured"]["pending_count"],
            "open_conflicts": open_conflicts,
            "explained_downtime_hours": round(
                _union_hours(_clip(downtime_intervals, start, end)), 2
            ),
            "season_dates": [self.rulebook.season_start.isoformat(), self.rulebook.season_end.isoformat()],
        }
        evidence = self._evidence_ids(gates)
        return {
            "station": station,
            "version": version,
            "unit_date": unit_date,
            "gates": gates,
            "status": status,
            "absence_eligible": absence_eligible,
            "trend_eligible": trend_eligible,
            "issues": issues,
            "feature_vector": feature_vector,
            "coverage_summary": summary,
            "evidence_record_ids": evidence,
        }

    # ------------------------------------------------------------------- gates
    def _device_gate(
        self, unit_date, start, end, frames, heartbeats, downtime_intervals, downtimes=()
    ) -> dict:
        req = self.req["device"]
        unexplained: list[dict[str, Any]] = []
        evidence = (
            [r.record.id for r in frames]
            + [r.record.id for r in heartbeats]
            + [r.record.id for r in downtimes]
        )
        day_intervals = _clip(downtime_intervals, start, end)
        downtime_hours = _union_hours(day_intervals)
        frames_sorted = sorted(
            frames, key=lambda r: (r.record.payload.get("frame_seq", 0), r.effective_time)
        )
        gaps: list[tuple[datetime, datetime]] = []
        for prev, nxt in zip(frames_sorted, frames_sorted[1:]):
            prev_seq = prev.record.payload.get("frame_seq")
            next_seq = nxt.record.payload.get("frame_seq")
            if (
                prev_seq is not None
                and next_seq is not None
                and int(next_seq) - int(prev_seq) > 1
            ):
                gaps.append((prev.effective_time, nxt.effective_time))
        heartbeat_times = sorted(r.effective_time for r in heartbeats)
        for prev, nxt in zip(heartbeat_times, heartbeat_times[1:]):
            if hours_between(prev, nxt) > req["max_heartbeat_gap_hours"]:
                gaps.append((prev, nxt))

        if heartbeats and not frames:
            gaps.append((start, end))
        if frames and not heartbeats:
            unexplained.append(
                {
                    "kind": "no_heartbeat",
                    "detail": "当日有图像帧但无设备心跳，无法确认设备可用时段",
                }
            )

        unexplained_hours = 0.0
        for gap_start, gap_end in gaps:
            gap_hours = hours_between(gap_start, gap_end)
            if gap_hours < req["min_gap_hours"]:
                continue
            if _covered(day_intervals, gap_start, gap_end):
                continue
            unexplained_hours += gap_hours
            unexplained.append(
                {
                    "kind": "frame_gap",
                    "start": gap_start.isoformat(),
                    "end": gap_end.isoformat(),
                    "hours": round(gap_hours, 2),
                    "detail": "连续图像帧缺失且停机日志未覆盖该时段",
                }
            )

        available_hours = max(0.0, 24.0 - downtime_hours - unexplained_hours)
        available_ratio = round(available_hours / 24.0, 3)
        threshold = {
            "target_hours": req["target_available_hours"],
            "min_hours": req["min_available_hours"],
            "target_ratio": req["target_available_ratio"],
            "min_ratio": req["min_available_ratio"],
            "max_unexplained_gap_hours": req["max_heartbeat_gap_hours"],
        }
        measured = {
            "available_hours": round(available_hours, 2),
            "available_ratio": available_ratio,
            "explained_downtime_hours": round(downtime_hours, 2),
            "unexplained_gap_hours": round(unexplained_hours, 2),
            "frame_count": len(frames),
            "heartbeat_count": len(heartbeats),
            "unexplained_gaps": unexplained,
            "downtime_record_count": len(downtime_intervals),
        }
        if unexplained or (frames and not heartbeats):
            status = "pending"
            explanation = (
                "设备在季节窗口内有心跳/图像，但存在未由停机日志覆盖的连续帧缺失，"
                "可用性待核；不据此判定物种未出现。"
            )
        elif len(frames) == 0 and len(heartbeats) == 0:
            status = "fail"
            explanation = "当日无设备心跳、无图像帧、无停机记录，设备可用性无法成立。"
        elif available_hours < req["min_available_hours"] or available_ratio < req["min_available_ratio"]:
            status = "fail"
            explanation = (
                f"可用 {available_hours:.1f}h，低于最低 {req['min_available_hours']}h，"
                "设备可用时段不足以支持未发现分析。"
            )
        elif available_hours < req["target_available_hours"] or available_ratio < req["target_available_ratio"]:
            status = "restricted"
            explanation = (
                f"可用 {available_hours:.1f}h，达到最低但未达目标 {req['target_available_hours']}h，"
                "设备覆盖受限。"
            )
        else:
            status = "pass"
            explanation = f"设备可用 {available_hours:.1f}h，停机日志完整覆盖，达到目标。"
        return _finding(
            "device",
            "DEVICE_AVAILABILITY",
            status,
            measured,
            threshold,
            explanation,
            f"规则册 {self.rulebook.rulebook.code} v{self.rulebook.version_number} · 设备可用性条款",
            evidence,
        )

    def _time_gate(self, recs: list[TimedRecord], device: dict) -> dict:
        req = self.req["time"]
        trusted = [r for r in recs if not r.time_issue and r.record.record_type not in {"station_metadata"}]
        untrusted = [r for r in recs if r.time_issue]
        calibrated = [r for r in recs if r.corrected]
        late = [r for r in recs if r.late]
        in_window = [r for r in trusted if self.season_start_dt <= r.effective_time < self.season_end_dt]
        measured = {
            "records_total": len(trusted) + len(untrusted),
            "untrusted_count": len(untrusted),
            "calibrated_count": len(calibrated),
            "late_delivery_count": len(late),
            "in_season_ratio": round(len(in_window) / max(1, len(trusted)), 3),
            "tolerance_seconds": req["tolerance_seconds"],
            "issues": [r.time_issue for r in untrusted][:5],
            "late_delivery_examples": [
                {
                    "record_id": r.record.id,
                    "external_id": r.record.external_id,
                    "received_delay_hours": round(
                        (ensure_utc(r.record.received_time) - ensure_utc(r.record.event_time)).total_seconds() / 3600,
                        1,
                    ),
                }
                for r in late[:5]
            ],
        }
        threshold = {
            "tolerance_seconds": req["tolerance_seconds"],
            "require_verifiable_calibration": True,
            "late_delivery_hours": req["late_delivery_hours"],
        }
        evidence = [r.record.id for r in untrusted] + [r.record.id for r in calibrated]
        if untrusted and not calibrated:
            status = "fail"
            explanation = (
                f"{len(untrusted)} 条记录设备时间与可信时间源相差超过 {req['tolerance_seconds']}s，"
                "且不存在可验证校时关系，不能据此满足季节窗口。"
            )
        elif untrusted and calibrated:
            status = "restricted"
            explanation = (
                "部分记录存在时钟偏差，已按可验证校时关系回算；仍有记录未能校时，时间可信度受限。"
            )
        elif not trusted and recs:
            status = "fail"
            explanation = "无可用时间记录。"
        else:
            status = "pass"
            explanation = (
                f"时间记录均在容差内，{len(calibrated)} 条按可验证校时回算；"
                f"{len(late)} 条迟到送达（不改变事件时间）。"
                if calibrated
                else f"时间记录均在容差内；{len(late)} 条迟到送达（不改变事件时间）。"
            )
        return _finding(
            "time",
            "TIME_TRUST",
            status,
            measured,
            threshold,
            explanation,
            f"规则册 {self.rulebook.rulebook.code} v{self.rulebook.version_number} · 时间校准条款",
            evidence,
        )

    def _space_gate(self, recs, frames, tracks, version, station, unit_date) -> dict:
        req = self.req["space"]
        segments = self.route_segments_for(station, unit_date)
        evidence = [r.record.id for r in tracks]
        best: dict[str, Any] | None = None
        edge_only = False
        precision_ambiguous = False
        precision_used = 0.0
        segment_coverages: list[dict[str, Any]] = []
        from shapely.geometry import Point

        from .utils import station_buffer

        buffer_geom = station_buffer(version)
        for track in tracks:
            geom = track_geometry(track.record.payload)
            if geom is None:
                continue
            m = analyze_track(geom, version)
            for seg in segments:
                coverage = segment_coverage(geom, version, seg)
                segment_coverages.append(
                    {
                        "segment_code": seg.get("code"),
                        "segment_name": seg.get("name"),
                        "coverage": coverage,
                        "track_record_id": track.record.id,
                    }
                )
            if best is None or m["coverage_ratio"] > best["coverage_ratio"]:
                best = m
            precision = track.record.coord_precision_m or 0.0
            if m["penetration_depth_m"] <= req["edge_penetration_m"]:
                # The track only skims the buffer boundary. Whether that counts as
                # "route actually passed" depends on GPS precision vs buffer width.
                edge_only = True
                precision_used = precision
                if precision > version.buffer_width_m:
                    precision_ambiguous = True

        # Route coverage is the best coverage across the station's required segments.
        route_coverage = 0.0
        if segment_coverages:
            per_segment: dict[str, float] = {}
            for row in segment_coverages:
                key = row["segment_code"] or ""
                per_segment[key] = max(per_segment.get(key, 0.0), row["coverage"])
            route_coverage = round(sum(per_segment.values()) / max(1, len(per_segment)), 3)
        best_r = best or {
            "track_length_m": 0.0,
            "inside_length_m": 0.0,
            "distance_to_buffer_m": None,
            "penetration_depth_m": 0.0,
            "coverage_ratio": 0.0,
            "buffer_width_m": version.buffer_width_m,
        }

        material_in = 0
        material_total = 0
        for frame in frames:
            material_total += 1
            lat = frame.record.latitude
            lon = frame.record.longitude
            if lat is None or lon is None:
                continue
            if buffer_geom.covers(Point(lon, lat)):
                material_in += 1
        in_buffer_ratio = round(material_in / material_total, 3) if material_total else 0.0
        precision_ok = not precision_ambiguous

        measured = {
            "route_coverage_ratio": route_coverage,
            "segment_coverages": segment_coverages[:8],
            "track_length_m": best_r["track_length_m"],
            "inside_length_m": best_r["inside_length_m"],
            "distance_to_buffer_m": best_r["distance_to_buffer_m"],
            "penetration_depth_m": best_r["penetration_depth_m"],
            "buffer_width_m": version.buffer_width_m,
            "track_precision_m": precision_used or None,
            "in_buffer_ratio": in_buffer_ratio,
            "material_in_buffer": material_in,
            "material_total": material_total,
            "precision_ok": precision_ok,
            "station_version": version.version_number,
            "edge_only": edge_only,
        }
        threshold = {
            "target_route_coverage_ratio": req["target_route_coverage_ratio"],
            "min_route_coverage_ratio": req["min_route_coverage_ratio"],
            "edge_penetration_m": req["edge_penetration_m"],
            "min_in_buffer_ratio": req["min_in_buffer_ratio"],
            "buffer_width_m": version.buffer_width_m,
        }
        if precision_ambiguous:
            status = "pending"
            explanation = (
                "轨迹仅穿过缓冲区边缘，最近进入深度 "
                f"{best_r['penetration_depth_m']:.0f}m，且定位精度半径 {precision_used:.0f}m "
                f"大于缓冲区宽度 {version.buffer_width_m:.0f}m，空间待核；"
                "路线边缘经过不能视为有效覆盖。"
            )
        elif not tracks:
            status = "fail"
            explanation = "当日无巡护轨迹记录，路线实际经过无法成立。"
        elif route_coverage < req["min_route_coverage_ratio"]:
            status = "fail"
            explanation = (
                f"路线实际经过覆盖 {route_coverage:.0%}，低于最低 "
                f"{req['min_route_coverage_ratio']:.0%}。"
            )
        elif route_coverage < req["target_route_coverage_ratio"]:
            status = "restricted"
            explanation = (
                f"路线覆盖 {route_coverage:.0%}，低于目标 "
                f"{req['target_route_coverage_ratio']:.0%}，空间覆盖受限。"
            )
        elif in_buffer_ratio < req["min_in_buffer_ratio"] and material_total:
            status = "restricted"
            explanation = (
                f"仅 {in_buffer_ratio:.0%} 素材坐标落在站点缓冲区内，空间归属受限。"
            )
        else:
            status = "pass"
            explanation = (
                f"路线在站点版本 v{version.version_number} 缓冲区内覆盖 "
                f"{route_coverage:.0%}，最深进入 {best_r['penetration_depth_m']:.0f}m。"
            )
        return _finding(
            "space",
            "SPATIAL_COVERAGE",
            status,
            measured,
            threshold,
            explanation,
            f"规则册 {self.rulebook.rulebook.code} v{self.rulebook.version_number} · 空间覆盖条款",
            evidence,
        )

    def _reading_gate(self, recs, frames, ctx: StationContext) -> dict:
        req = self.req["reading"]
        target_codes = {t["code"] for t in self.rulebook.target_species}
        min_conf = req["min_confidence"]
        determinations = []
        for frame in frames:
            determinations.extend(ctx.determinations_by_material.get(frame.record.id, []))

        qualifying = [d for d in determinations if d.confidence >= min_conf]
        available_target = [d for d in qualifying if d.species_code in target_codes]
        pending_materials: list[TimedRecord] = []
        conflicts: list[dict[str, Any]] = []
        for frame in frames:
            dets = ctx.determinations_by_material.get(frame.record.id, [])
            by_species: dict[str, list[Determination]] = defaultdict(list)
            for d in dets:
                by_species[d.species_code].append(d)
            conflicted = False
            for species, group in by_species.items():
                present = [d for d in group if d.verdict == "present"]
                absent = [d for d in group if d.verdict == "absent"]
                if present and absent and all(d.confidence < min_conf for d in group):
                    conflicted = True
                    conflicts.append(
                        {
                            "material_record_id": frame.record.id,
                            "material_external_id": frame.record.external_id,
                            "species_code": species,
                            "sources": [
                                {
                                    "source": d.source,
                                    "analyst": d.analyst,
                                    "verdict": d.verdict,
                                    "confidence": d.confidence,
                                    "determination_id": d.id,
                                }
                                for d in group
                            ],
                        }
                    )
            if conflicted:
                continue
            if not any(d.confidence >= min_conf for d in dets):
                pending_materials.append(frame)

        total = max(1, len(frames))
        pending_ratio = round(len(pending_materials) / total, 3)
        measured = {
            "determinations_total": len(determinations),
            "available_observations": len(available_target),
            "target_present": sum(1 for d in available_target if d.verdict == "present"),
            "target_absent": sum(1 for d in available_target if d.verdict == "absent"),
            "pending_count": len(pending_materials),
            "pending_ratio": pending_ratio,
            "open_conflicts": len(conflicts),
            "conflicts": conflicts,
            "pending_materials": [
                {"record_id": f.record.id, "external_id": f.record.external_id}
                for f in pending_materials
            ],
            "determination_evidence": [d.id for d in determinations],
            "material_evidence": [f.record.id for f in frames],
        }
        threshold = {
            "min_confidence": min_conf,
            "max_pending_ratio": req["max_pending_ratio"],
            "min_available_observations": req["min_available_observations"],
        }
        evidence = [f.record.id for f in frames] + [d.id for d in determinations]
        if conflicts:
            status = "pending"
            explanation = (
                f"{len(conflicts)} 份素材存在互斥判读且均未达置信门槛 {min_conf}，"
                "保留冲突，不作已发现或未发现结论。"
            )
        elif not frames:
            status = "fail"
            explanation = "当日无图像素材，判读充分性无法成立。"
        elif len(available_target) < req["min_available_observations"]:
            status = "pending" if pending_materials else "fail"
            explanation = (
                f"目标物种可用判读 {len(available_target)} 条，低于最低 "
                f"{req['min_available_observations']} 条；"
                f"{len(pending_materials)} 份素材待判读，不能视为物种未出现。"
            )
        elif pending_ratio > req["max_pending_ratio"]:
            status = "restricted"
            explanation = (
                f"待判读素材占比 {pending_ratio:.0%}，高于上限 {req['max_pending_ratio']:.0%}，"
                "判读覆盖受限。"
            )
        else:
            status = "pass"
            explanation = (
                f"{len(available_target)} 条达门槛的目标物种判读（出现 "
                f"{measured['target_present']} 条），待判读 {len(pending_materials)} 份，未超上限。"
            )
        return _finding(
            "reading",
            "READING_ADEQUACY",
            status,
            measured,
            threshold,
            explanation,
            f"规则册 {self.rulebook.rulebook.code} v{self.rulebook.version_number} · 判读充分性条款",
            evidence,
        )

    # ---------------------------------------------------------------- helpers
    def _collect_issues(self, gates, frames, tracks, recs, ctx: StationContext) -> list[dict]:
        issues: list[dict[str, Any]] = []
        for gate in gates:
            if gate["status"] in {"pending", "fail", "restricted"}:
                issues.append(
                    {
                        "gate": gate["gate"],
                        "gate_label": gate["gate_label"],
                        "status": gate["status"],
                        "explanation": gate["explanation"],
                    }
                )
        for r in recs:
            for det in ctx.determinations_by_material.get(r.record.id, []):
                if det.verdict == "uncertain":
                    issues.append(
                        {
                            "gate": "reading",
                            "gate_label": "素材判读",
                            "status": "note",
                            "explanation": f"判读 {det.id} 标记为不确定（{det.source}），未计入可用观测。",
                        }
                    )
        return issues

    @staticmethod
    def _evidence_ids(gates) -> list[int]:
        ids: set[int] = set()
        for gate in gates:
            for value in gate["evidence"]:
                if isinstance(value, int):
                    ids.add(value)
        return sorted(ids)


def persist_unit(
    db: Session,
    run_id: int,
    built: dict[str, Any],
    rulebook: RulebookVersion,
    model_id: int | None = None,
) -> SurveyUnit:
    unit = SurveyUnit(
        run_id=run_id,
        station_id=built["station"].id,
        zone_id=built["station"].zone_id,
        unit_date=built["unit_date"],
        station_version_id=built["version"].id,
        rulebook_version_id=rulebook.id,
        model_version_id=model_id,
        coverage_summary=built["coverage_summary"],
        feature_vector=built["feature_vector"],
        gate_status={g["gate"]: g["status"] for g in built["gates"]},
        data_issues=built["issues"],
    )
    db.add(unit)
    db.flush()
    for gate in built["gates"]:
        db.add(
            GateCheck(
                unit_id=unit.id,
                gate=gate["gate"],
                requirement_code=gate["requirement_code"],
                evaluation_round=1,
                status=gate["status"],
                measured=gate["measured"],
                threshold=gate["threshold"],
                explanation=gate["explanation"],
                rule_ref=gate["rule_ref"],
                evidence=gate["evidence"],
            )
        )
    return unit


def collect_records_for_unit(db: Session, unit: SurveyUnit) -> list[ArchiveRecord]:
    """All raw records that the unit's gate evidence points at, plus same-day station records."""
    evidence_ids: set[int] = set()
    for gate in db.execute(select(GateCheck).where(GateCheck.unit_id == unit.id)).scalars():
        for value in gate.evidence or []:
            if isinstance(value, int):
                evidence_ids.add(value)
    if evidence_ids:
        return list(
            db.execute(select(ArchiveRecord).where(ArchiveRecord.id.in_(evidence_ids))).scalars()
        )
    return []
