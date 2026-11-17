"""Reproduce the full spatial publication cycle on the seeded sample data.

Steps (all against the configured database, no external map platform):
  1. publish spring 绿尾虹雉 under sensitivity v1 / habitat v1;
  2. publish autumn under v1 so the two seasons share extents;
  3. raise sensitivity to critical (v2) and revise public habitat (v2);
  4. confirm affected publications went stale and split riparian habitat,
     re-check the affected partitions and re-deliver;
  5. print the common-extent comparison and the public/route leak checks.

Run:  DATABASE_URL=... python -m scripts.spatial_demo
or:   cd backend && PYTHONPATH=. .venv/bin/python scripts/spatial_demo.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    PartitionRegion,
    PublicationRun,
    RecipientScope,
    SurveyRun,
)
from app.spatial import create_publication
from app.spatial_datasets import activate
from app.spatial_delivery import (
    build_public_payload,
    compare_publications,
    internal_report,
    publish,
)


def _run(db, season_code: str) -> SurveyRun:
    return db.execute(
        select(SurveyRun).where(SurveyRun.season_code == season_code).order_by(SurveyRun.id)
    ).scalars().first()


def _publication(db, season_code: str, scope: str, species: str) -> PublicationRun:
    return db.execute(
        select(PublicationRun)
        .where(PublicationRun.season_code == season_code)
        .where(PublicationRun.scope_code == scope)
        .where(PublicationRun.species_code == species)
        .order_by(PublicationRun.id.desc())
    ).scalars().first()


def _summary(db, publication: PublicationRun) -> dict:
    regions = list(
        db.execute(
            select(PartitionRegion)
            .where(PartitionRegion.publication_id == publication.id)
            .order_by(PartitionRegion.id)
        ).scalars()
    )
    return {
        "code": publication.code,
        "status": publication.status,
        "checks_passed": publication.checks_passed,
        "sensitivity": publication.sensitivity_level,
        "habitat_version": publication.habitat_version,
        "regions": [
            {
                "region_code": region.region_code,
                "status": region.status,
                "extent_key": region.extent_key,
                "candidate_habitat_m2": region.candidate_habitat_m2,
                "width_m": region.width_m,
                "observation_count": region.observation_count,
                "effective_hours": region.effective_hours,
                "route_km": round(region.route_length_m / 1000, 2),
                "reasons": [reason["code"] for reason in region.reasons or []],
            }
            for region in regions
        ],
    }


def main() -> None:
    db = SessionLocal()
    try:
        spring = _run(db, "2026-SPRING")
        autumn = _run(db, "2026-AUTUMN")
        if spring is None or autumn is None:
            raise SystemExit("缺少春季或秋季运行，请先执行 seed")

        species = "LOPHOPHORUS"
        spring_pub = _publication(db, "2026-SPRING", "PUBLIC", species)
        if spring_pub is None:
            spring_pub = create_publication(
                db,
                species_code=species,
                scope_code="PUBLIC",
                season_code="2026-SPRING",
                run=spring,
                actor="yq.li",
                note="春季发布",
            )
            if spring_pub.checks_passed:
                publish(db, spring_pub, "yq.li", "春季公众地图与数据文件")
            db.commit()
        autumn_pub = _publication(db, "2026-AUTUMN", "PUBLIC", species)
        if autumn_pub is None:
            autumn_pub = create_publication(
                db,
                species_code=species,
                scope_code="PUBLIC",
                season_code="2026-AUTUMN",
                run=autumn,
                actor="yq.li",
                note="秋季发布",
            )
            if autumn_pub.checks_passed:
                publish(db, autumn_pub, "yq.li", "秋季公众地图与数据文件")
            db.commit()

        print("== 1-2. 春季/秋季发布（敏感级别 high，生境 v1）==")
        print(json.dumps(_summary(db, spring_pub), ensure_ascii=False, indent=2))
        print(json.dumps(_summary(db, autumn_pub), ensure_ascii=False, indent=2))

        comparison = compare_publications(db, spring_pub, autumn_pub)
        print("== 共同范围比较 ==")
        print(
            json.dumps(
                {
                    "shared_extent_count": comparison["shared_extent_count"],
                    "comparable_rows": comparison["comparable_rows"],
                    "only_first": comparison["only_first"],
                    "only_second": comparison["only_second"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        print("== 3. 提高敏感级别 + 修订生境 ==")
        sensitivity = activate(db, "sensitivity", 2)
        habitat = activate(db, "habitat", 2)
        db.commit()
        from app.spatial_delivery import invalidate_after_dataset_change

        stale_a = invalidate_after_dataset_change(db, "sensitivity", sensitivity.version)
        stale_b = invalidate_after_dataset_change(db, "habitat", habitat.version)
        db.commit()
        print(
            "stale publications:",
            sorted({p.code for p in stale_a + stale_b}),
        )

        print("== 4. 重新检查受影响分区并重新生成交付 ==")
        spring_v2 = create_publication(
            db,
            species_code=species,
            scope_code="PUBLIC",
            season_code="2026-SPRING",
            run=spring,
            actor="yq.li",
            note="敏感级别提升为 critical、生境 v2 后重新检查",
        )
        if spring_v2.checks_passed:
            publish(db, spring_v2, "yq.li", "提升敏感级别后的春季公众地图")
        autumn_v2 = create_publication(
            db,
            species_code=species,
            scope_code="PUBLIC",
            season_code="2026-AUTUMN",
            run=autumn,
            actor="yq.li",
            note="敏感级别提升为 critical、生境 v2 后重新检查",
        )
        if autumn_v2.checks_passed:
            publish(db, autumn_v2, "yq.li", "提升敏感级别后的秋季公众地图")
        db.commit()
        print(json.dumps(_summary(db, spring_v2), ensure_ascii=False, indent=2))
        print(json.dumps(_summary(db, autumn_v2), ensure_ascii=False, indent=2))

        comparison_v2 = compare_publications(db, spring_v2, autumn_v2)
        print("== 提级后的共同范围比较 ==")
        print(
            json.dumps(
                {
                    "shared_extent_count": comparison_v2["shared_extent_count"],
                    "comparable_rows": comparison_v2["comparable_rows"],
                    "only_first": comparison_v2["only_first"],
                    "only_second": comparison_v2["only_second"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        print("== 5. 公开载荷字段检查 ==")
        payload = build_public_payload(db, spring_v2)
        text = json.dumps(payload["map"], ensure_ascii=False)
        forbidden = ["latitude", "longitude", "station", "s3://", "record_id"]
        leaks = [word for word in forbidden if word in text]
        print("leaks:", leaks)
        report = internal_report(db, spring_v2)
        print(
            "reconciliation:",
            json.dumps(
                {
                    "observation_totals": report["reconciliation"]["observation_totals"],
                    "effort_totals": report["reconciliation"]["effort_totals"],
                    "route_totals": report["reconciliation"]["route_totals"],
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
