"""Response serializers (plain dicts keep the API contract explicit and stable)."""
from __future__ import annotations

from typing import Any

from .config import RECORD_TYPE_LABELS, STATUS_LABELS
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
    Rulebook,
    RulebookVersion,
    Station,
    StationVersion,
    SurveyRun,
    SurveyUnit,
    TrendReference,
    Zone,
)


def iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def zone_out(zone: Zone) -> dict[str, Any]:
    return {
        "id": zone.id,
        "code": zone.code,
        "name": zone.name,
        "description": zone.description,
    }


def station_version_out(version: StationVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "version_number": version.version_number,
        "boundary": version.boundary,
        "buffer_width_m": version.buffer_width_m,
        "centroid": {"lat": version.centroid_lat, "lon": version.centroid_lon},
        "elevation_min_m": version.elevation_min_m,
        "elevation_max_m": version.elevation_max_m,
        "effective_from": version.effective_from.isoformat(),
        "effective_to": iso(version.effective_to),
        "change_reason": version.change_reason,
    }


def station_out(station: Station, include_versions: bool = False) -> dict[str, Any]:
    data = {
        "id": station.id,
        "code": station.code,
        "name": station.name,
        "zone_id": station.zone_id,
        "zone": {"code": station.zone.code, "name": station.zone.name} if station.zone else None,
        "latitude": station.latitude,
        "longitude": station.longitude,
        "elevation_min_m": station.elevation_min_m,
        "elevation_max_m": station.elevation_max_m,
        "status": station.status,
        "version_count": len(station.versions),
    }
    if include_versions:
        data["versions"] = [station_version_out(v) for v in station.versions]
    return data


def rulebook_version_out(version: RulebookVersion, rulebook: Rulebook | None = None) -> dict[str, Any]:
    return {
        "id": version.id,
        "rulebook_id": version.rulebook_id,
        "rulebook_code": rulebook.code if rulebook else (version.rulebook.code if version.rulebook else None),
        "rulebook_name": rulebook.name if rulebook else (version.rulebook.name if version.rulebook else None),
        "version_number": version.version_number,
        "label": version.label,
        "season_code": version.season_code,
        "season_start": version.season_start.isoformat(),
        "season_end": version.season_end.isoformat(),
        "status": version.status,
        "target_species": version.target_species,
        "requirements": version.requirements,
        "route_segments": version.route_segments,
        "elevation_bands": version.elevation_bands,
        "notes": version.notes,
        "created_at": iso(version.created_at),
    }


def record_out(
    record: ArchiveRecord, include_revisions: bool = False, station_code: str | None = None
) -> dict[str, Any]:
    data = {
        "id": record.id,
        "record_type": record.record_type,
        "record_type_label": RECORD_TYPE_LABELS.get(record.record_type, record.record_type),
        "external_id": record.external_id,
        "station_id": record.station_id,
        "station_code": station_code,
        "zone_id": record.zone_id,
        "station_version_id": record.station_version_id,
        "event_time": iso(record.event_time),
        "received_time": iso(record.received_time),
        "source_device": record.source_device,
        "device_type": record.device_type,
        "latitude": record.latitude,
        "longitude": record.longitude,
        "coord_precision_m": record.coord_precision_m,
        "payload": record.payload,
        "payload_hash": record.payload_hash,
        "revision": record.revision,
        "record_status": record.record_status,
        "superseded_by_id": record.superseded_by_id,
        "created_by": record.created_by,
        "created_at": iso(record.created_at),
    }
    if include_revisions:
        data["revisions"] = [revision_out(r) for r in record.revisions]
    return data


def revision_out(rev: RecordRevision) -> dict[str, Any]:
    return {
        "id": rev.id,
        "revision_number": rev.revision_number,
        "payload": rev.payload,
        "payload_hash": rev.payload_hash,
        "received_time": iso(rev.received_time),
        "changed_by": rev.changed_by,
        "change_reason": rev.change_reason,
        "created_at": iso(rev.created_at),
    }


def determination_out(det: Determination) -> dict[str, Any]:
    return {
        "id": det.id,
        "material_record_id": det.material_record_id,
        "source": det.source,
        "species_code": det.species_code,
        "verdict": det.verdict,
        "confidence": det.confidence,
        "analyst": det.analyst,
        "revision": det.revision,
        "status": det.status,
        "superseded_by_id": det.superseded_by_id,
        "notes": det.notes,
        "created_at": iso(det.created_at),
    }


def gate_out(gate: GateCheck) -> dict[str, Any]:
    return {
        "id": gate.id,
        "gate": gate.gate,
        "requirement_code": gate.requirement_code,
        "status": gate.status,
        "measured": gate.measured,
        "threshold": gate.threshold,
        "explanation": gate.explanation,
        "rule_ref": gate.rule_ref,
        "evidence": gate.evidence,
        "evaluated_at": iso(gate.evaluated_at),
    }


def unit_out(unit: SurveyUnit, station: Station | None = None) -> dict[str, Any]:
    return {
        "id": unit.id,
        "run_id": unit.run_id,
        "station_id": unit.station_id,
        "station_code": station.code if station else None,
        "station_name": station.name if station else None,
        "zone_id": unit.zone_id,
        "unit_date": unit.unit_date.isoformat(),
        "station_version_id": unit.station_version_id,
        "rulebook_version_id": unit.rulebook_version_id,
        "model_version_id": unit.model_version_id,
        "coverage_summary": unit.coverage_summary,
        "feature_vector": unit.feature_vector,
        "gate_status": unit.gate_status,
        "data_issues": unit.data_issues,
        "assembled_at": iso(unit.assembled_at),
    }


def certificate_out(cert: Certificate) -> dict[str, Any]:
    return {
        "id": cert.id,
        "unit_id": cert.unit_id,
        "run_id": cert.run_id,
        "certificate_no": cert.certificate_no,
        "version": cert.version,
        "status": cert.status,
        "status_label": STATUS_LABELS.get(cert.status, cert.status),
        "absence_eligible": cert.absence_eligible,
        "trend_eligible": cert.trend_eligible,
        "review_status": cert.review_status,
        "review_status_label": STATUS_LABELS.get(cert.review_status, cert.review_status),
        "review_reason": cert.review_reason,
        "summary": cert.summary,
        "gate_snapshot": cert.gate_snapshot,
        "conclusions": cert.conclusions,
        "issued_at": iso(cert.issued_at),
        "superseded_by_id": cert.superseded_by_id,
    }


def estimate_out(est: ProbabilityEstimate, model: ProbabilityModel | None = None) -> dict[str, Any]:
    return {
        "id": est.id,
        "unit_id": est.unit_id,
        "model_id": est.model_id,
        "model_code": model.code if model else None,
        "model_name": model.name if model else None,
        "model_version": model.version if model else None,
        "model_status": model.status if model else None,
        "threshold": model.threshold if model else None,
        "probability": est.probability,
        "interval_low": est.interval_low,
        "interval_high": est.interval_high,
        "threshold_pass": est.threshold_pass,
        "conclusion": est.conclusion,
        "variants": est.variants,
        "assumptions": est.assumptions,
        "created_at": iso(est.created_at),
    }


def model_out(model: ProbabilityModel) -> dict[str, Any]:
    return {
        "id": model.id,
        "code": model.code,
        "name": model.name,
        "version": model.version,
        "status": model.status,
        "threshold": model.threshold,
        "calibration_sets": model.calibration_sets,
        "parameters": model.parameters,
        "threshold_review": model.threshold_review,
        "notes": model.notes,
        "created_at": iso(model.created_at),
    }


def conflict_out(conflict: ReadingConflict) -> dict[str, Any]:
    return {
        "id": conflict.id,
        "unit_id": conflict.unit_id,
        "run_id": conflict.run_id,
        "material_record_id": conflict.material_record_id,
        "station_id": conflict.station_id,
        "zone_id": conflict.zone_id,
        "species_code": conflict.species_code,
        "sources": conflict.sources,
        "detail": conflict.detail,
        "status": conflict.status,
        "resolution": conflict.resolution,
        "detected_at": iso(conflict.detected_at),
        "resolved_at": iso(conflict.resolved_at),
    }


def trend_out(ref: TrendReference, affected_by_review: bool = False) -> dict[str, Any]:
    return {
        "id": ref.id,
        "reference_code": ref.reference_code,
        "purpose": ref.purpose,
        "purpose_label": "正式未发现结论" if ref.purpose == "absence" else "趋势输入",
        "unit_id": ref.unit_id,
        "certificate_id": ref.certificate_id,
        "probability_estimate_id": ref.probability_estimate_id,
        "status": ref.status,
        "affected_by_review": affected_by_review,
        "review_note": (
            "该引用签发时所用证书已有复审版本，保留原运行快照；如需继续用于趋势，应复核后重新签发。"
            if affected_by_review
            else ""
        ),
        "snapshot": ref.snapshot,
        "created_by": ref.created_by,
        "created_at": iso(ref.created_at),
        "suspended_reason": ref.suspended_reason,
        "suspended_at": iso(ref.suspended_at),
        "note": ref.note,
    }


def run_out(run: SurveyRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "run_code": run.run_code,
        "label": run.label,
        "season_code": run.season_code,
        "season_start": run.season_start.isoformat(),
        "season_end": run.season_end.isoformat(),
        "status": run.status,
        "rulebook_version_id": run.rulebook_version_id,
        "model_version_id": run.model_version_id,
        "created_by": run.created_by,
        "note": run.note,
        "created_at": iso(run.created_at),
    }


def audit_out(event: AuditEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "action": event.action,
        "actor": event.actor,
        "detail": event.detail,
        "created_at": iso(event.created_at),
    }


def user_out(user) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
    }
