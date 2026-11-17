from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32), default="researcher")  # researcher | auditor | viewer
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")

    stations: Mapped[list["Station"]] = relationship(back_populates="zone")


class Station(Base):
    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    elevation_min_m: Mapped[float] = mapped_column(Float)
    elevation_max_m: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    zone: Mapped[Zone] = relationship(back_populates="stations")
    versions: Mapped[list["StationVersion"]] = relationship(
        back_populates="station", order_by="StationVersion.version_number"
    )


class StationVersion(Base):
    __tablename__ = "station_versions"
    __table_args__ = (UniqueConstraint("station_id", "version_number", name="uq_station_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    boundary: Mapped[dict[str, Any]] = mapped_column(JSONB)  # GeoJSON Polygon
    buffer_width_m: Mapped[float] = mapped_column(Float)
    centroid_lat: Mapped[float] = mapped_column(Float)
    centroid_lon: Mapped[float] = mapped_column(Float)
    elevation_min_m: Mapped[float] = mapped_column(Float)
    elevation_max_m: Mapped[float] = mapped_column(Float)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    change_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    station: Mapped[Station] = relationship(back_populates="versions")


class Rulebook(Base):
    __tablename__ = "rulebooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id"), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    versions: Mapped[list["RulebookVersion"]] = relationship(
        back_populates="rulebook", order_by="RulebookVersion.version_number"
    )


class RulebookVersion(Base):
    __tablename__ = "rulebook_versions"
    __table_args__ = (UniqueConstraint("rulebook_id", "version_number", name="uq_rulebook_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rulebook_id: Mapped[int] = mapped_column(ForeignKey("rulebooks.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(128))
    season_code: Mapped[str] = mapped_column(String(32))
    season_start: Mapped[date] = mapped_column(Date)
    season_end: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), default="active")  # draft | active | retired
    target_species: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    requirements: Mapped[dict[str, Any]] = mapped_column(JSONB)
    route_segments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    elevation_bands: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    rulebook: Mapped[Rulebook] = relationship(back_populates="versions")


class ArchiveRecord(Base):
    __tablename__ = "archive_records"
    __table_args__ = (
        Index("ix_archive_station_event", "station_id", "event_time"),
        Index("ix_archive_type_event", "record_type", "event_time"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    record_type: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"))
    station_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("station_versions.id"), nullable=True
    )
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_device: Mapped[str] = mapped_column(String(64))
    device_type: Mapped[str] = mapped_column(String(32), default="camera")
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    coord_precision_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    payload_hash: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    record_status: Mapped[str] = mapped_column(String(32), default="active")
    superseded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("archive_records.id"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(64), default="ingest")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    revisions: Mapped[list["RecordRevision"]] = relationship(
        back_populates="record", order_by="RecordRevision.revision_number"
    )


class RecordRevision(Base):
    __tablename__ = "record_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("archive_records.id"), index=True)
    revision_number: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    payload_hash: Mapped[str] = mapped_column(String(64))
    received_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    changed_by: Mapped[str] = mapped_column(String(64))
    change_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    record: Mapped[ArchiveRecord] = relationship(back_populates="revisions")


class Determination(Base):
    __tablename__ = "determinations"
    __table_args__ = (Index("ix_det_material_status", "material_record_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    material_record_id: Mapped[int] = mapped_column(ForeignKey("archive_records.id"))
    source_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("archive_records.id"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(64))  # analyst | model | partner
    species_code: Mapped[str] = mapped_column(String(64), index=True)
    verdict: Mapped[str] = mapped_column(String(32))  # present | absent | uncertain
    confidence: Mapped[float] = mapped_column(Float)
    analyst: Mapped[str] = mapped_column(String(64), default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="active")  # active | superseded
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("determinations.id"), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SurveyRun(Base):
    __tablename__ = "survey_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_code: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(128))
    season_code: Mapped[str] = mapped_column(String(32))
    season_start: Mapped[date] = mapped_column(Date)
    season_end: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), default="active")  # active | superseded
    rulebook_version_id: Mapped[int] = mapped_column(ForeignKey("rulebook_versions.id"))
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("probability_models.id"), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    units: Mapped[list["SurveyUnit"]] = relationship(back_populates="run")


class SurveyUnit(Base):
    __tablename__ = "survey_units"
    __table_args__ = (
        UniqueConstraint("run_id", "station_id", "zone_id", "unit_date", name="uq_survey_unit"),
        Index("ix_unit_station_date", "station_id", "unit_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("survey_runs.id"), index=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"))
    unit_date: Mapped[date] = mapped_column(Date)
    station_version_id: Mapped[int] = mapped_column(ForeignKey("station_versions.id"))
    rulebook_version_id: Mapped[int] = mapped_column(ForeignKey("rulebook_versions.id"))
    model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("probability_models.id"), nullable=True
    )
    assembled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    coverage_summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    feature_vector: Mapped[dict[str, Any]] = mapped_column(JSONB)
    gate_status: Mapped[dict[str, Any]] = mapped_column(JSONB)
    data_issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)

    run: Mapped[SurveyRun] = relationship(back_populates="units")
    gates: Mapped[list["GateCheck"]] = relationship(back_populates="unit")
    certificate: Mapped["Certificate | None"] = relationship(back_populates="unit", uselist=False)


class GateCheck(Base):
    __tablename__ = "gate_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("survey_units.id"), index=True)
    gate: Mapped[str] = mapped_column(String(16))
    requirement_code: Mapped[str] = mapped_column(String(64))
    evaluation_round: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32))  # pass | restricted | fail | pending
    measured: Mapped[dict[str, Any]] = mapped_column(JSONB)
    threshold: Mapped[dict[str, Any]] = mapped_column(JSONB)
    explanation: Mapped[str] = mapped_column(Text)
    rule_ref: Mapped[str] = mapped_column(String(128))
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    unit: Mapped[SurveyUnit] = relationship(back_populates="gates")


class Certificate(Base):
    __tablename__ = "certificates"
    __table_args__ = (Index("ix_cert_unit_status", "unit_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("survey_units.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("survey_runs.id"), index=True)
    certificate_no: Mapped[str] = mapped_column(String(64), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32))  # valid | restricted | invalid | pending
    absence_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    trend_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    review_status: Mapped[str] = mapped_column(String(32), default="normal")  # normal | under_review
    review_reason: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text)
    gate_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    conclusions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("certificates.id"), nullable=True)

    unit: Mapped[SurveyUnit] = relationship(back_populates="certificate")


class ReadingConflict(Base):
    __tablename__ = "reading_conflicts"

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("survey_units.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("survey_runs.id"), index=True)
    material_record_id: Mapped[int] = mapped_column(ForeignKey("archive_records.id"))
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"))
    species_code: Mapped[str] = mapped_column(String(64))
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    detail: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="open")  # open | resolved
    resolution: Mapped[str] = mapped_column(Text, default="")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProbabilityModel(Base):
    __tablename__ = "probability_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="active")  # active | under_review | retired
    threshold: Mapped[float] = mapped_column(Float)
    calibration_sets: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    threshold_review: Mapped[dict[str, Any]] = mapped_column(JSONB)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProbabilityEstimate(Base):
    __tablename__ = "probability_estimates"

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("survey_units.id"), index=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("probability_models.id"))
    probability: Mapped[float] = mapped_column(Float)
    interval_low: Mapped[float] = mapped_column(Float)
    interval_high: Mapped[float] = mapped_column(Float)
    threshold_pass: Mapped[bool] = mapped_column(Boolean)
    conclusion: Mapped[str] = mapped_column(String(32))  # supports_partial | below_threshold
    variants: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    assumptions: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrendReference(Base):
    __tablename__ = "trend_references"

    id: Mapped[int] = mapped_column(primary_key=True)
    reference_code: Mapped[str] = mapped_column(String(64), unique=True)
    purpose: Mapped[str] = mapped_column(String(32))  # absence | trend
    unit_id: Mapped[int] = mapped_column(ForeignKey("survey_units.id"))
    certificate_id: Mapped[int | None] = mapped_column(ForeignKey("certificates.id"), nullable=True)
    probability_estimate_id: Mapped[int | None] = mapped_column(
        ForeignKey("probability_estimates.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="active")  # active | suspended | revoked
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    suspended_reason: Mapped[str] = mapped_column(Text, default="")
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------
# Spatial publication: habitat, sensitivity, grids, partitions and delivery
# --------------------------------------------------------------------------
class DatasetRevision(Base):
    __tablename__ = "dataset_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset: Mapped[str] = mapped_column(String(32), index=True)  # habitat | suitability | sensitivity | scopes
    version: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    source_file: Mapped[str] = mapped_column(String(256), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HabitatPolygon(Base):
    """Public habitat boundary. Never derived from a nest point or station position."""

    __tablename__ = "habitat_polygons"
    __table_args__ = (Index("ix_habitat_class_version", "habitat_class", "dataset_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_version: Mapped[int] = mapped_column(Integer, index=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128))
    habitat_class: Mapped[str] = mapped_column(String(32))
    geometry: Mapped[dict[str, Any]] = mapped_column(JSONB)
    area_m2: Mapped[float] = mapped_column(Float)
    elevation_min_m: Mapped[float] = mapped_column(Float)
    elevation_max_m: Mapped[float] = mapped_column(Float)
    connectivity_group: Mapped[str] = mapped_column(String(64), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class SpeciesSuitability(Base):
    __tablename__ = "species_suitability"
    __table_args__ = (
        UniqueConstraint("species_code", "dataset_version", name="uq_suitability_species_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    species_code: Mapped[str] = mapped_column(String(64), index=True)
    dataset_version: Mapped[int] = mapped_column(Integer)
    habitat_classes: Mapped[list[str]] = mapped_column(JSONB)
    elevation_min_m: Mapped[float] = mapped_column(Float)
    elevation_max_m: Mapped[float] = mapped_column(Float)
    notes: Mapped[str] = mapped_column(Text, default="")


class SpeciesSensitivity(Base):
    __tablename__ = "species_sensitivity"
    __table_args__ = (
        UniqueConstraint("species_code", "dataset_version", name="uq_sensitivity_species_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    species_code: Mapped[str] = mapped_column(String(64), index=True)
    species_name: Mapped[str] = mapped_column(String(128))
    level: Mapped[str] = mapped_column(String(16))  # low | medium | high | critical
    dataset_version: Mapped[int] = mapped_column(Integer)
    notes: Mapped[str] = mapped_column(Text, default="")


class RecipientScope(Base):
    __tablename__ = "recipient_scopes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    audience: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(128))
    external: Mapped[bool] = mapped_column(Boolean, default=True)
    allowed_levels: Mapped[list[str]] = mapped_column(JSONB)
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB)
    dataset_version: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[str] = mapped_column(Text, default="")


class GridCell(Base):
    """Fixed base grid. Cells are retained on every region for cross-season joins."""

    __tablename__ = "grid_cells"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    ix: Mapped[int] = mapped_column(Integer)
    iy: Mapped[int] = mapped_column(Integer)
    cell_size_m: Mapped[float] = mapped_column(Float)
    grid_version: Mapped[int] = mapped_column(Integer, default=1)
    geometry: Mapped[dict[str, Any]] = mapped_column(JSONB)
    area_m2: Mapped[float] = mapped_column(Float)


class PublicationRun(Base):
    __tablename__ = "publication_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    scope_code: Mapped[str] = mapped_column(String(32), index=True)
    species_code: Mapped[str] = mapped_column(String(64), index=True)
    season_code: Mapped[str] = mapped_column(String(32), index=True)
    survey_run_id: Mapped[int | None] = mapped_column(ForeignKey("survey_runs.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="checked")
    # building | checked | published | stale | stopped | superseded
    sensitivity_level: Mapped[str] = mapped_column(String(16))
    sensitivity_version: Mapped[int] = mapped_column(Integer)
    habitat_version: Mapped[int] = mapped_column(Integer)
    suitability_version: Mapped[int] = mapped_column(Integer)
    scope_version: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB)
    thresholds_from_disclosure: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    region_count: Mapped[int] = mapped_column(Integer, default=0)
    published_region_count: Mapped[int] = mapped_column(Integer, default=0)
    unattributed_observations: Mapped[int] = mapped_column(Integer, default=0)
    checks_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    stop_reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("publication_runs.id"), nullable=True
    )
    stale_reason: Mapped[str] = mapped_column(Text, default="")


class PartitionRegion(Base):
    __tablename__ = "partition_regions"
    __table_args__ = (
        UniqueConstraint("publication_id", "region_code", name="uq_region_code"),
        Index("ix_region_publication_status", "publication_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publication_runs.id"), index=True)
    region_code: Mapped[str] = mapped_column(String(32))
    extent_key: Mapped[str] = mapped_column(String(32))
    component_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))  # published | withheld
    geometry: Mapped[dict[str, Any]] = mapped_column(JSONB)
    display_geometry: Mapped[dict[str, Any]] = mapped_column(JSONB)
    area_m2: Mapped[float] = mapped_column(Float)
    candidate_habitat_m2: Mapped[float] = mapped_column(Float)
    width_m: Mapped[float] = mapped_column(Float)
    base_cell_count: Mapped[int] = mapped_column(Integer)
    candidate_cells: Mapped[list[str]] = mapped_column(JSONB, default=list)
    display_cell_m: Mapped[float] = mapped_column(Float, default=100.0)
    display_area_m2: Mapped[float] = mapped_column(Float, default=0.0)
    effort_known: Mapped[bool] = mapped_column(Boolean, default=False)
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    sensitivity_level: Mapped[str] = mapped_column(String(16))
    threshold_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    observation_count: Mapped[int] = mapped_column(Integer, default=0)
    present_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_materials: Mapped[int] = mapped_column(Integer, default=0)
    effective_hours: Mapped[float] = mapped_column(Float, default=0.0)
    route_length_m: Mapped[float] = mapped_column(Float, default=0.0)
    effort_unknown_units: Mapped[int] = mapped_column(Integer, default=0)
    effort_units: Mapped[int] = mapped_column(Integer, default=0)
    merged_effort_windows: Mapped[int] = mapped_column(Integer, default=0)
    expanded_for_prior: Mapped[bool] = mapped_column(Boolean, default=False)
    occurrence_rate: Mapped[float | None] = mapped_column(Float, nullable=True)


class RegionCell(Base):
    __tablename__ = "region_cells"
    __table_args__ = (UniqueConstraint("region_id", "cell_id", name="uq_region_cell"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    region_id: Mapped[int] = mapped_column(ForeignKey("partition_regions.id"), index=True)
    cell_id: Mapped[int] = mapped_column(ForeignKey("grid_cells.id"))
    cell_code: Mapped[str] = mapped_column(String(32))
    candidate_habitat_m2: Mapped[float] = mapped_column(Float, default=0.0)
    observation_count: Mapped[int] = mapped_column(Integer, default=0)
    present_count: Mapped[int] = mapped_column(Integer, default=0)
    effective_hours: Mapped[float] = mapped_column(Float, default=0.0)
    route_length_m: Mapped[float] = mapped_column(Float, default=0.0)


class RegionObservation(Base):
    __tablename__ = "region_observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publication_runs.id"), index=True)
    region_id: Mapped[int | None] = mapped_column(
        ForeignKey("partition_regions.id"), nullable=True, index=True
    )
    cell_id: Mapped[int | None] = mapped_column(ForeignKey("grid_cells.id"), nullable=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("archive_records.id"))
    external_id: Mapped[str] = mapped_column(String(128))
    material_key: Mapped[str] = mapped_column(String(128), default="")
    source: Mapped[str] = mapped_column(String(64), default="")
    species_code: Mapped[str] = mapped_column(String(64))
    verdict: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float)
    disposition: Mapped[str] = mapped_column(String(24))  # included | duplicate | unattributed
    reason: Mapped[str] = mapped_column(String(128), default="")


class RegionEffort(Base):
    """One merged valid-device block per region. Overlapping windows are counted once."""

    __tablename__ = "region_effort"

    id: Mapped[int] = mapped_column(primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publication_runs.id"), index=True)
    region_id: Mapped[int | None] = mapped_column(
        ForeignKey("partition_regions.id"), nullable=True, index=True
    )
    unit_ids: Mapped[list[int]] = mapped_column(JSONB, default=list)
    station_ids: Mapped[list[int]] = mapped_column(JSONB, default=list)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    hours: Mapped[float] = mapped_column(Float)
    merged_intervals: Mapped[int] = mapped_column(Integer, default=1)
    disposition: Mapped[str] = mapped_column(String(24))  # included | unknown | excluded
    reason: Mapped[str] = mapped_column(String(128), default="")


class RegionRoute(Base):
    __tablename__ = "region_routes"

    id: Mapped[int] = mapped_column(primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publication_runs.id"), index=True)
    region_id: Mapped[int | None] = mapped_column(
        ForeignKey("partition_regions.id"), nullable=True, index=True
    )
    cell_id: Mapped[int | None] = mapped_column(ForeignKey("grid_cells.id"), nullable=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("archive_records.id"))
    length_m: Mapped[float] = mapped_column(Float, default=0.0)
    disposition: Mapped[str] = mapped_column(String(24))  # included | duplicate | excluded
    reason: Mapped[str] = mapped_column(String(128), default="")


class PublicationCheck(Base):
    __tablename__ = "publication_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publication_runs.id"), index=True)
    check_code: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # pass | fail
    detail: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PublicMaterial(Base):
    """Ledger of every map/file ever released. Withdrawal never erases the range."""

    __tablename__ = "public_materials"
    __table_args__ = (Index("ix_material_scope_species", "scope_code", "species_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publication_runs.id"), index=True)
    scope_code: Mapped[str] = mapped_column(String(32))
    species_code: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))  # map | download
    material_code: Mapped[str] = mapped_column(String(64), unique=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    sensitivity_level: Mapped[str] = mapped_column(String(16))
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB)
    regions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | stale | superseded | withdrawn
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    csv_text: Mapped[str] = mapped_column(Text, default="")
    superseded_by_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
