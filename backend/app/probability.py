"""Calibrated detection-probability estimator (alternative view of partial coverage).

The model is a deterministic logistic transform of a weighted coverage score that
was calibrated on historical survey units. It is shown next to the rule certificate
and never replaces it: the UI marks the estimate and its sensitivity band as model
inference, not confirmed field coverage.
"""
from __future__ import annotations

import math
from typing import Any

from .models import ProbabilityModel

# Feature order is frozen with the model version; changing it requires a new model row.
FEATURES = (
    "device_available_ratio",
    "route_coverage_ratio",
    "observation_count",
    "pending_material_ratio",
    "in_buffer_ratio",
    "precision_ok",
    "time_ok",
    "conflict_free",
)

# Time trust and unresolved reading conflicts are hard gates in the model too: a unit
# whose event time cannot be trusted, or whose material has an unresolved conflict, has
# no defensible coverage value regardless of how much was covered.
GATING_FEATURES = ("time_ok", "conflict_free")
WEIGHTED_FEATURES = tuple(f for f in FEATURES if f not in GATING_FEATURES)


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def feature_value(features: dict[str, Any], name: str) -> float:
    raw = features.get(name, 0.0)
    if name == "observation_count":
        raw = min(1.0, float(raw or 0) / 4.0)
    elif name == "pending_material_ratio":
        raw = 1.0 - float(raw or 0.0)
    return max(0.0, min(1.0, float(raw or 0.0)))


def coverage_score(features: dict[str, Any], params: dict[str, Any]) -> float:
    weights = params["weights"]
    additive = sum(float(weights.get(name, 0.0)) * feature_value(features, name) for name in WEIGHTED_FEATURES)
    gate = 1.0
    for name in GATING_FEATURES:
        gate *= feature_value(features, name)
    return additive * gate


def _z(score: float, params: dict[str, Any], pivot_delta: float = 0.0) -> float:
    return (score - float(params.get("pivot", 0.6)) - pivot_delta) * float(params.get("slope", 8.0))


def estimate(
    model: ProbabilityModel,
    features: dict[str, Any],
    calibration_delta: float = 0.0,
) -> dict[str, Any]:
    """Estimate coverage value for one unit.

    ``calibration_delta`` shifts the calibration pivot (a positive value means a
    stricter calibration set, i.e. fewer units clear the threshold).
    """
    params = model.parameters
    prepared = {name: feature_value(features, name) for name in FEATURES}
    score = coverage_score(features, params)
    z = _z(score, params)
    probability = _sigmoid(z)
    k = float(params.get("interval_z", 1.28))
    scale = float(params.get("interval_scale", 0.9))
    low = _sigmoid(z - k * scale)
    high = _sigmoid(z + k * scale)

    shifted = _sigmoid(_z(score, params, calibration_delta))
    strict_threshold = min(0.99, round(model.threshold + 0.1, 2))
    variants = [
        {
            "key": "baseline",
            "label": "当前校准集",
            "probability": round(probability, 3),
            "low": round(low, 3),
            "high": round(high, 3),
            "passes": probability >= model.threshold,
        },
        {
            "key": "prior_calibration",
            "label": "上一版校准集",
            "probability": round(shifted, 3),
            "low": round(_sigmoid(_z(score, params, calibration_delta) - k * scale), 3),
            "high": round(_sigmoid(_z(score, params, calibration_delta) + k * scale), 3),
            "passes": shifted >= model.threshold,
        },
        {
            "key": "strict_threshold",
            "label": f"阈值上浮至 {strict_threshold:.2f}",
            "probability": round(probability, 3),
            "low": round(low, 3),
            "high": round(high, 3),
            "passes": probability >= strict_threshold,
        },
    ]
    conclusion = "supports_partial" if probability >= model.threshold else "below_threshold"
    assumptions = [
        "检测概率由历史样本校准，反映部分覆盖的模型推算价值，不构成已确认的实地覆盖。",
        "仅在规则证书之外展示；正式未发现结论必须使用规则证书。",
        "敏感区间随校准集与阈值复核变化，引用时保留模型版本与阈值。",
    ]
    return {
        "probability": round(probability, 3),
        "interval_low": round(low, 3),
        "interval_high": round(high, 3),
        "threshold_pass": probability >= model.threshold,
        "conclusion": conclusion,
        "variants": variants,
        "assumptions": assumptions,
        "coverage_score": round(score, 4),
        "features": prepared,
    }
