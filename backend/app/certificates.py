"""Certificate adjudicator: turns gate checks into survey certificates."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import (
    STATUS_INVALID,
    STATUS_PENDING,
    STATUS_RESTRICTED,
    STATUS_VALID,
)
from .models import Certificate, GateCheck, SurveyRun, SurveyUnit


def certificate_status_from_gates(gates: list[GateCheck]) -> str:
    statuses = {g.status for g in gates}
    if "pending" in statuses:
        return STATUS_PENDING
    if "fail" in statuses:
        return STATUS_INVALID
    if "restricted" in statuses:
        return STATUS_RESTRICTED
    return STATUS_VALID


def purpose_flags(status: str, gates: list[GateCheck], open_conflicts: int) -> tuple[bool, bool]:
    blocked = "pending" in {g.status for g in gates}
    absence = status == STATUS_VALID and not blocked and open_conflicts == 0
    trend = status in {STATUS_VALID, STATUS_RESTRICTED} and not blocked
    return absence, trend


def summarize(status: str, gates: list[GateCheck], unit: SurveyUnit) -> str:
    headline = {
        STATUS_VALID: "有效调查：设备、时间、空间与判读门槛均满足，可用于正式未发现结论。",
        STATUS_RESTRICTED: "受限调查：主要门槛满足但存在受限项，可用于趋势输入，不作正式未发现结论。",
        STATUS_INVALID: "未有效调查：存在未满足的调查门槛，不能简化为物种未出现。",
        STATUS_PENDING: "待核：存在待核实证据，证书暂停用途，待核清后重新签发。",
    }[status]
    failing = [g for g in gates if g.status in {"pending", "fail", "restricted"}]
    if not failing:
        return headline
    detail = "；".join(f"{g.gate}：{g.explanation}" for g in failing[:3])
    return f"{headline} {detail}"


def issue_certificate(
    db: Session,
    run: SurveyRun,
    unit: SurveyUnit,
    gates: list[GateCheck],
    sequence: int,
    conclusions: dict[str, Any] | None = None,
    review: dict[str, Any] | None = None,
    version: int = 1,
) -> Certificate:
    status = certificate_status_from_gates(gates)
    open_conflicts = int(unit.coverage_summary.get("open_conflicts", 0))
    absence, trend = purpose_flags(status, gates, open_conflicts)
    review = review or {}
    if review.get("review_status") == "under_review":
        absence = False
        trend = False
    # Deep-copied into plain Python types so later edits to a gate row can never
    # rewrite what an already-issued certificate recorded.
    snapshot = json.loads(
        json.dumps(
            [
                {
                    "gate": g.gate,
                    "status": g.status,
                    "requirement_code": g.requirement_code,
                    "measured": g.measured,
                    "threshold": g.threshold,
                    "explanation": g.explanation,
                    "rule_ref": g.rule_ref,
                }
                for g in gates
            ]
        )
    )
    base_no = f"CERT-{run.run_code}-{unit.station_id:03d}-{unit.unit_date:%Y%m%d}"
    cert = Certificate(
        unit_id=unit.id,
        run_id=run.id,
        certificate_no=base_no if version <= 1 else f"{base_no}-V{version}",
        version=max(1, version),
        status=status,
        absence_eligible=absence,
        trend_eligible=trend,
        review_status=review.get("review_status", "normal"),
        review_reason=review.get("review_reason", ""),
        summary=summarize(status, gates, unit),
        gate_snapshot=snapshot,
        conclusions=json.loads(json.dumps(conclusions or {})),
        issued_at=datetime.now(timezone.utc),
    )
    db.add(cert)
    db.flush()
    return cert


def current_certificate(db: Session, unit_id: int) -> Certificate | None:
    return (
        db.execute(
            select(Certificate)
            .where(Certificate.unit_id == unit_id)
            .where(Certificate.superseded_by_id.is_(None))
            .order_by(Certificate.version.desc())
        )
        .scalars()
        .first()
    )


def mark_under_review(
    db: Session,
    unit_ids: list[int],
    reason: str,
    actor: str,
    conclusions_by_unit: dict[int, dict[str, Any]] | None = None,
) -> list[Certificate]:
    """Issue a review certificate version for affected units.

    The previously issued certificate keeps its original snapshot and is only
    annotated with a successor pointer, so the run that produced it still shows
    the certificate exactly as issued. New trend references are blocked while the
    current certificate version is under review.

    ``conclusions_by_unit`` lets the caller supply conclusions recomputed against
    the *current* evidence, so the review version does not inherit a stale
    absence verdict from the superseded certificate.
    """
    from .models import AuditEvent, TrendReference
    from .models import utcnow

    conclusions_by_unit = conclusions_by_unit or {}
    changed: list[Certificate] = []
    for unit_id in sorted(set(unit_ids)):
        current = current_certificate(db, unit_id)
        if current is None or current.review_status == "under_review":
            continue
        refreshed = conclusions_by_unit.get(unit_id)
        if refreshed is None:
            refreshed = json.loads(json.dumps(current.conclusions or {}))
            analysis = refreshed.get("absence_analysis")
            if analysis:
                analysis["supported"] = False
                analysis["species_note"] = "判读修订改变出现判定，复审结论未定；不复用原证书的未发现结论"
            refreshed["under_review"] = True
        review_cert = Certificate(
            unit_id=current.unit_id,
            run_id=current.run_id,
            certificate_no=f"{current.certificate_no}-R{current.version + 1}",
            version=current.version + 1,
            status=STATUS_PENDING,
            absence_eligible=False,
            trend_eligible=False,
            review_status="under_review",
            review_reason=reason,
            summary=f"复审中：{reason} 原证书 v{current.version} 保留为历史签发记录。",
            gate_snapshot=current.gate_snapshot,
            conclusions=refreshed,
            issued_at=utcnow(),
        )
        db.add(review_cert)
        db.flush()
        current.superseded_by_id = review_cert.id
        # References issued against the superseded certificate are paused, not deleted:
        # their snapshots stay readable for the run that produced them.
        refs = list(
            db.execute(
                select(TrendReference)
                .where(TrendReference.certificate_id == current.id)
                .where(TrendReference.status == "active")
            ).scalars()
        )
        for ref in refs:
            ref.status = "suspended"
            ref.suspended_reason = f"证书 {current.certificate_no} 复审中：{reason}"
            ref.suspended_at = utcnow()
        db.add(
            AuditEvent(
                entity_type="certificate",
                entity_id=review_cert.id,
                action="certificate.under_review",
                actor=actor,
                detail={
                    "previous_certificate_id": current.id,
                    "reason": reason,
                    "suspended_references": [r.reference_code for r in refs],
                },
            )
        )
        changed.append(review_cert)
    return changed
