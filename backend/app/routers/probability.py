from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ProbabilityEstimate, ProbabilityModel, SurveyUnit
from ..probability import FEATURES, estimate as run_estimate
from ..security import get_current_user
from ..serializers import model_out

router = APIRouter(prefix="/probability", tags=["probability"])


@router.post("/models/{model_id}/evaluate")
def evaluate(
    model_id: int,
    unit_id: int,
    calibration_delta: float = 0.0,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """Re-run the estimator for one unit, optionally swapping the calibration set.

    Used by the UI's sensitivity panel: it never writes a certificate and never
    enables an absence conclusion.
    """
    model = db.get(ProbabilityModel, model_id)
    if model is None:
        raise HTTPException(404, "模型不存在")
    unit = db.get(SurveyUnit, unit_id)
    if unit is None:
        raise HTTPException(404, "调查单元不存在")
    result = run_estimate(model, unit.feature_vector, calibration_delta=calibration_delta)
    result.update(
        {
            "model_id": model.id,
            "model_code": model.code,
            "model_version": model.version,
            "model_status": model.status,
            "threshold": model.threshold,
            "calibration_sets": model.calibration_sets,
            "unit_id": unit.id,
            "features_used": list(FEATURES),
            "unit_features": unit.feature_vector,
            "calibration_delta": calibration_delta,
            "purpose": "trend_only",
            "disclaimer": "概率结果仅用于趋势研究参考，不是已确认的实地覆盖，不能替代规则证书。",
        }
    )
    return result


@router.get("/models/{model_id}/threshold-sensitivity")
def threshold_sensitivity(
    model_id: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    model = db.get(ProbabilityModel, model_id)
    if model is None:
        raise HTTPException(404, "模型不存在")
    thresholds = [0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
    probabilities = list(
        db.execute(
            select(ProbabilityEstimate.probability).where(
                ProbabilityEstimate.model_id == model.id
            )
        ).scalars()
    )
    rows = []
    for threshold in thresholds:
        passed = sum(1 for p in probabilities if p >= threshold)
        rows.append(
            {
                "threshold": threshold,
                "delta": round(threshold - model.threshold, 3),
                "units_passing": passed,
                "delta_from_current": passed - sum(1 for p in probabilities if p >= model.threshold),
            }
        )
    return {
        "model": model_out(model),
        "rows": rows,
        "note": "阈值上浮会减少可用样本；阈值复核成本与模型校准集变化需同时登记。",
    }
