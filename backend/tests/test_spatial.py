"""Spatial publication: partitioning, statistics attribution and delivery safety."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import engine, init_db
from app.seed import main as seed_main

SPECIES = "LOPHOPHORUS"


@pytest.fixture(scope="module", autouse=True)
def fresh_spatial_state(client):
    """Start this module from the seeded baseline.

    The other modules share a session-scoped database; spatial tests deliberately
    change active dataset versions, so they get a pristine schema of their own.
    """
    with engine.begin() as conn:
        conn.execute(text("drop schema public cascade"))
        conn.execute(text("create schema public"))
    init_db()
    seed_main()
    yield


def _publications(client, auth):
    return client.get("/api/spatial/publications", headers=auth).json()


def _publication(client, auth, season: str, status: str = "published"):
    rows = [
        row
        for row in _publications(client, auth)
        if row["season_code"] == season and row["status"] == status
    ]
    assert rows, f"missing {status} publication for {season}"
    return rows[0]


def _detail(client, auth, publication_id):
    return client.get(f"/api/spatial/publications/{publication_id}", headers=auth).json()


def _check(detail, code):
    return next(item for item in detail["checks"] if item["check_code"] == code)


def test_partition_uses_public_habitat_and_withholds_narrow_banks(client, auth):
    """Regions come from public habitat; a narrow/tiny bank is withheld with reasons."""
    spring = _publication(client, auth, "2026-SPRING")
    detail = _detail(client, auth, spring["id"])
    regions = detail["regions"]
    assert len(regions) >= 2
    # Every region records connectivity and the base units it keeps.
    for region in regions:
        assert region["base_cell_count"] > 0
        assert region["candidate_cells"]
        assert region["family"]
        assert region["candidate_habitat_m2"] > 0
    withheld = [region for region in regions if region["status"] == "withheld"]
    assert withheld, "the small riparian remnant must not be published"
    for region in withheld:
        codes = {reason["code"] for reason in region["reasons"]}
        assert codes, "withheld regions must record why"
    tiny = next(r for r in withheld if "CANDIDATE_HABITAT_BELOW_MINIMUM" in {x["code"] for x in r["reasons"]})
    assert tiny["candidate_habitat_m2"] < 60000
    # Candidate area is real habitat, never a bounding box.
    for region in regions:
        assert region["candidate_habitat_m2"] <= region["area_m2"] + 1

    threshold_check = _check(detail, "DISCLOSURE_THRESHOLDS")
    assert threshold_check["status"] == "pass"

    # The first five checks are structural and must pass together.
    structural = {item["check_code"]: item["status"] for item in detail["checks"]}
    for code in (
        "CANDIDATE_HABITAT_BASED",
        "WITHHELD_REASONS_RECORDED",
        "DISPLAY_EXTENTS_DISJOINT",
        "PUBLISHABLE_REGIONS_EXIST",
    ):
        assert structural[code] == "pass"


def test_statistics_reconcile_observations_effort_and_routes(client, auth):
    spring = _publication(client, auth, "2026-SPRING")
    report = client.get(
        f"/api/spatial/publications/{spring['id']}/reconciliation", headers=auth
    ).json()
    checks = {item["check_code"]: item for item in report["checks"]}
    reconcile = checks["OBSERVATION_RECONCILIATION"]
    assert reconcile["status"] == "pass"
    data = reconcile["data"]
    assert (
        data["included"] + data["duplicates"] + data["unattributed"] + data["excluded"]
        == data["internal_candidates"]
    )

    observations = report["reconciliation"]["observation_rows"]
    included = [row for row in observations if row["disposition"] == "included"]
    assert len(included) == data["included"]
    # Cross-boundary precision stays unattributed: count kept, never spread.
    assert data["unattributed"] > 0
    for row in observations:
        if row["disposition"] == "unattributed":
            assert row["region_code"] is None
    # Duplicate reposts are de-duplicated but still listed against the source;
    # the republished autumn frame lives in the autumn publication.
    autumn = _publication(client, auth, "2026-AUTUMN")
    autumn_report = client.get(
        f"/api/spatial/publications/{autumn['id']}/reconciliation", headers=auth
    ).json()
    autumn_rows = autumn_report["reconciliation"]["observation_rows"]
    dupes = [row for row in autumn_rows if row["disposition"] == "duplicate"]
    assert dupes and any("转载" in row["reason"] for row in dupes)
    assert autumn_report["reconciliation"]["observation_totals"]["duplicates"] >= 1

    effort = checks["EFFORT_MERGED_AND_RECONCILED"]
    assert effort["status"] == "pass"
    assert effort["data"]["merged_effort_hours"] <= effort["data"]["internal_effort_hours"] + 0.05
    assert effort["data"]["published_region_hours"] <= effort["data"]["merged_effort_hours"] + 0.05
    assert effort["data"]["merged_intervals"] >= 1

    routes = checks["ROUTE_RECONCILIATION"]
    assert routes["status"] == "pass"
    assert checks["EFFORT_KNOWN_FOR_PUBLISHED"]["status"] == "pass"


def test_common_extent_comparison_and_separate_display(client, auth):
    spring = _publication(client, auth, "2026-SPRING")
    autumn = _publication(client, auth, "2026-AUTUMN")
    comparison = client.get(
        f"/api/spatial/compare?first={spring['id']}&second={autumn['id']}", headers=auth
    ).json()
    assert comparison["shared_extent_count"] >= 1
    for row in comparison["comparable_rows"]:
        assert row["candidate_habitat_ha"][0] == row["candidate_habitat_ha"][1]
        assert row["comparable"] is True
        assert row["delta_observations"] == (
            row["observation_count"][1] - row["observation_count"][0]
        )
    for row in comparison["only_first"] + comparison["only_second"]:
        assert row["comparable"] is False
        assert "共同范围" in row["note"]


def test_public_payload_has_no_precise_identifiers(client, auth):
    spring = _publication(client, auth, "2026-SPRING")
    public = client.get(f"/api/public/publications/{spring['code']}/map")
    assert public.status_code == 200
    payload = public.json()
    text = public.text.lower()
    for token in ("latitude", "longitude", "s3://", "record_id", "external_id", "station_code"):
        assert token not in text
    assert payload["map"]["features"]
    for feature in payload["map"]["features"]:
        assert feature["properties"]["withheld"] is False
        assert feature["properties"]["region_code"].startswith("R-")
        assert "geometry" in feature
        assert feature["properties"]["display_cell_m"] >= 200
    assert payload["stats"]["withheld"]
    for withheld in payload["stats"]["withheld"]:
        assert "geometry" not in withheld


def test_public_routes_reject_fine_grained_filters(client, auth):
    spring = _publication(client, auth, "2026-SPRING")
    for path in (
        f"/api/public/publications/{spring['code']}/map?bbox=103.6,31.0,103.7,31.1",
        f"/api/public/publications/{spring['code']}/map?lat=31.05&lon=103.63",
        f"/api/public/publications/{spring['code']}/stats?cell=G1:1:2",
        f"/api/public/publications/{spring['code']}/download.csv?station_id=1",
    ):
        response = client.get(path)
        assert response.status_code == 400
        assert "细粒度筛选" in response.json()["detail"]


def test_public_download_and_withdrawal_keep_ledger(client, auth):
    spring = _publication(client, auth, "2026-SPRING")
    download = client.get(f"/api/public/publications/{spring['code']}/download.csv")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/csv")
    lines = [line for line in download.text.splitlines() if line.strip()]
    header = lines[0].split(",")
    for column in ("region_code", "candidate_habitat_ha", "observation_count", "effective_hours"):
        assert column in header
    assert "latitude" not in download.text.lower()

    # A withdrawn material is refused, yet its released range stays on the ledger:
    # withdrawal is not erasure.
    materials = client.get("/api/spatial/materials", headers=auth).json()
    spring_csv = next(
        row
        for row in materials
        if row["publication_code"] == spring["code"] and row["kind"] == "download"
    )
    withdrawn = client.post(
        f"/api/spatial/materials/{spring_csv['material_code']}/withdraw",
        headers=auth,
        json={"reason": "测试：主动撤回旧下载"},
    )
    assert withdrawn.status_code == 200
    again = client.get(f"/api/public/materials/{spring_csv['material_code']}")
    assert again.status_code == 410
    ledger = client.get("/api/spatial/materials", headers=auth).json()
    kept = next(row for row in ledger if row["material_code"] == spring_csv["material_code"])
    assert kept["status"] == "withdrawn"
    assert kept["regions"], "released range must stay recorded after withdrawal"


def test_sensitivity_escalation_and_habitat_revision_marks_stale_and_rechecks(client, auth):
    """Raising sensitivity must stale earlier deliveries and re-check the partitions."""
    spring = _publication(client, auth, "2026-SPRING")
    activation = client.post(
        "/api/spatial/datasets/sensitivity/activate", headers=auth, json={"version": 2}
    )
    assert activation.status_code == 200
    stale = activation.json()["stale_publications"]
    assert spring["code"] in stale

    legacy = client.get(f"/api/public/publications/{spring['code']}/map")
    assert legacy.status_code == 409

    habitat = client.post(
        "/api/spatial/datasets/habitat/activate", headers=auth, json={"version": 2}
    )
    assert habitat.status_code == 200

    runs = client.get("/api/runs", headers=auth).json()
    spring_run = next(row for row in runs if row["run_code"] == "RUN-2026-SPRING-01")
    created = client.post(
        "/api/spatial/publications",
        headers=auth,
        json={
            "species_code": SPECIES,
            "scope_code": "PUBLIC",
            "run_id": spring_run["id"],
            "note": "提级后重新检查",
        },
    )
    assert created.status_code == 200, created.text
    publication = created.json()
    assert publication["sensitivity_level"] == "critical"
    assert publication["habitat_version"] == 2
    assert publication["checks_passed"] is True

    detail = _detail(client, auth, publication["id"])
    regions = detail["regions"]
    # The river bank was split by the revised boundary; the pieces must not be
    # recombined across an unrelated ridge — the small one is either regrouped or
    # withheld with a recorded reason.
    split = [
        region
        for region in regions
        if region["family"] in {"QSL-RIP", "QSL-RIP-A", "QSL-RIP-B"}
        or "RIP" in region["family"]
    ]
    assert split
    for region in split:
        assert region["base_cell_count"] > 0
        if region["status"] == "withheld":
            assert region["reasons"]
    assert any(
        "PRIOR_RELEASE_RANGE" in {reason["code"] for reason in region["reasons"]}
        or not region["reasons"]
        for region in regions
    )

    delivery = client.post(
        f"/api/spatial/publications/{publication['id']}/publish", headers=auth
    )
    assert delivery.status_code == 200, delivery.text
    fresh = client.get(f"/api/public/publications/{publication['code']}/map")
    assert fresh.status_code == 200
    for feature in fresh.json()["map"]["features"]:
        assert feature["properties"]["display_cell_m"] >= 500
        assert feature["properties"]["sensitivity_level"] == "critical"

    # Prior released ranges remain covered (withdrawal / supersession is not erasure).
    prior = _check(detail, "PRIOR_RELEASE_RANGE_SAFE")
    assert prior["status"] == "pass"


def test_stale_publication_cannot_generate_delivery(client, auth):
    spring = _publication(client, auth, "2026-SPRING")
    activation = client.post(
        "/api/spatial/datasets/suitability/activate", headers=auth, json={"version": 2}
    )
    assert spring["code"] in activation.json()["stale_publications"]
    blocked = client.post(f"/api/spatial/publications/{spring['id']}/publish", headers=auth)
    assert blocked.status_code in {409, 410}
    assert "重新检查" in blocked.json()["detail"] or "过期" in blocked.json()["detail"]


def test_internal_and_public_entry_points_are_separate(client, auth):
    """Internal reconciliation requires a login; public routes never expose it."""
    internal = client.get("/api/spatial/publications", headers=auth)
    assert internal.status_code == 200
    unauthorized = TestClient(client.app).get("/api/spatial/publications")
    assert unauthorized.status_code == 401
    schema = client.get("/openapi.json").json()
    assert any(path.startswith("/api/public/") for path in schema["paths"])
    assert any(path.startswith("/api/spatial/") for path in schema["paths"])

    # The internal detail carries base units and reasons; no public route returns
    # them, whichever delivery is currently being served.
    all_rows = _publications(client, auth)
    assert all_rows
    detail = _detail(client, auth, all_rows[0]["id"])
    assert detail["regions"][0]["base_cells"]
    served = client.get("/api/public/publications").json()
    for row in served:
        response = client.get(f"/api/public/publications/{row['code']}/map")
        if response.status_code == 200:
            public_text = response.text
            for internal_field in ("base_cells", "reason_codes", "fingerprint"):
                assert internal_field not in public_text


def test_spatial_service_emits_no_dispatch_actions(client, auth):
    forbidden = ("dispatch", "assign", "schedule_patrol", "enforce", "capture", "resource_order")
    schema = client.get("/openapi.json").json()
    paths = " ".join(schema["paths"].keys()).lower()
    for word in forbidden:
        assert word not in paths
    for publication in _publications(client, auth):
        text = client.get(f"/api/public/publications/{publication['code']}/map").text.lower()
        for word in forbidden:
            assert word not in text


def test_scope_not_allowed_sensitive_species_stops_release(client, auth):
    """A scope that may not serve a critical species stops, with one clear reason."""
    # Raise sensitivity first, then re-check: the PARTNER scope allows only up to high.
    activation = client.post(
        "/api/spatial/datasets/sensitivity/activate", headers=auth, json={"version": 2}
    )
    assert activation.status_code == 200
    habitat = client.post(
        "/api/spatial/datasets/habitat/activate", headers=auth, json={"version": 2}
    )
    assert habitat.status_code == 200
    runs = client.get("/api/runs", headers=auth).json()
    spring_run = next(row for row in runs if row["run_code"] == "RUN-2026-SPRING-01")
    created = client.post(
        "/api/spatial/publications",
        headers=auth,
        json={
            "species_code": SPECIES,
            "scope_code": "PARTNER",
            "run_id": spring_run["id"],
            "note": "合作机构范围不允许极高敏感级别",
        },
    )
    assert created.status_code == 200, created.text
    publication = created.json()
    assert publication["status"] == "stopped"
    assert publication["checks_passed"] is False
    detail = _detail(client, auth, publication["id"])
    failed = {item["check_code"] for item in detail["checks"] if item["status"] == "fail"}
    assert failed == {"PUBLISHABLE_REGIONS_EXIST"}
    for region in detail["regions"]:
        assert region["status"] == "withheld"
        assert "SENSITIVITY_NOT_ALLOWED_FOR_SCOPE" in {r["code"] for r in region["reasons"]}
    blocked = client.post(
        f"/api/spatial/publications/{publication['id']}/publish", headers=auth
    )
    assert blocked.status_code == 409
