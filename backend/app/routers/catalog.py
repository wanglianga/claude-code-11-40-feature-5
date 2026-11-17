from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Certificate,
    ProbabilityEstimate,
    ProbabilityModel,
    ReadingConflict,
    Rulebook,
    RulebookVersion,
    Station,
    SurveyRun,
    SurveyUnit,
    TrendReference,
    Zone,
)
from ..serializers import (
    model_out,
    rulebook_version_out,
    run_out,
    station_out,
    zone_out,
)
from ..security import get_current_user

router = APIRouter(tags=["catalog"])


@router.get("/zones")
def list_zones(db: Session = Depends(get_db), _=Depends(get_current_user)):
    zones = db.execute(select(Zone).order_by(Zone.id)).scalars().all()
    result = []
    for zone in zones:
        data = zone_out(zone)
        data["station_count"] = db.execute(
            select(func.count()).select_from(Station).where(Station.zone_id == zone.id)
        ).scalar_one()
        result.append(data)
    return result


@router.get("/stations")
def list_stations(db: Session = Depends(get_db), _=Depends(get_current_user)):
    stations = db.execute(select(Station).order_by(Station.id)).scalars().all()
    return [station_out(s) for s in stations]


@router.get("/stations/{station_id}")
def get_station(station_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    station = db.get(Station, station_id)
    if station is None:
        raise HTTPException(404, "站点不存在")
    return station_out(station, include_versions=True)


@router.get("/rulebooks")
def list_rulebooks(db: Session = Depends(get_db), _=Depends(get_current_user)):
    versions = (
        db.execute(
            select(RulebookVersion).join(Rulebook).order_by(Rulebook.code, RulebookVersion.version_number.desc())
        )
        .scalars()
        .all()
    )
    return [rulebook_version_out(v) for v in versions]


@router.get("/models")
def list_models(db: Session = Depends(get_db), _=Depends(get_current_user)):
    models = db.execute(select(ProbabilityModel).order_by(ProbabilityModel.id)).scalars().all()
    usage = dict(
        db.execute(
            select(ProbabilityEstimate.model_id, func.count()).group_by(ProbabilityEstimate.model_id)
        ).all()
    )
    result = []
    for model in models:
        data = model_out(model)
        data["estimate_count"] = usage.get(model.id, 0)
        result.append(data)
    return result


@router.get("/models/{model_id}")
def get_model(model_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    model = db.get(ProbabilityModel, model_id)
    if model is None:
        raise HTTPException(404, "模型不存在")
    return model_out(model)


@router.get("/runs")
def list_runs(db: Session = Depends(get_db), _=Depends(get_current_user)):
    runs = db.execute(select(SurveyRun).order_by(SurveyRun.id.desc())).scalars().all()
    result = []
    for run in runs:
        data = run_out(run)
        data["unit_count"] = db.execute(
            select(func.count()).select_from(SurveyUnit).where(SurveyUnit.run_id == run.id)
        ).scalar_one()
        status_rows = db.execute(
            select(Certificate.status, func.count())
            .where(Certificate.run_id == run.id)
            .where(Certificate.superseded_by_id.is_(None))
            .group_by(Certificate.status)
        ).all()
        data["status_counts"] = {status: count for status, count in status_rows}
        data["open_conflicts"] = db.execute(
            select(func.count())
            .select_from(ReadingConflict)
            .where(ReadingConflict.run_id == run.id)
            .where(ReadingConflict.status == "open")
        ).scalar_one()
        data["trend_references"] = db.execute(
            select(func.count())
            .select_from(TrendReference)
            .join(SurveyUnit, TrendReference.unit_id == SurveyUnit.id)
            .where(SurveyUnit.run_id == run.id)
        ).scalar_one()
        result.append(data)
    return result
