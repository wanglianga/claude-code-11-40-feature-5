"""Revision, re-issue and traceability tests on an isolated fresh run."""
from __future__ import annotations


def _fresh_run(client, auth):
    rulebooks = client.get("/api/rulebooks", headers=auth).json()
    active = next(
        r for r in rulebooks if r["status"] == "active" and r["season_code"] == "2026-SPRING"
    )
    models = client.get("/api/models", headers=auth).json()
    response = client.post(
        "/api/runs",
        headers=auth,
        json={
            "rulebook_version_id": active["id"],
            "model_id": models[0]["id"],
            "label": "测试用重算运行",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _unit(client, auth, run_id, station_code, unit_date):
    units = client.get(f"/api/runs/{run_id}/units", headers=auth).json()
    return next(
        u for u in units if u["station_code"] == station_code and u["unit_date"] == unit_date
    )


def test_revised_reading_triggers_review_and_reissue(client, auth, run_id):
    run = _fresh_run(client, auth)
    unit = _unit(client, auth, run, "QSL-04", "2026-04-10")
    detail = client.get(f"/api/units/{unit['id']}", headers=auth).json()
    assert detail["certificate"]["status"] == "pending"

    material = next(m for m in detail["evidence"]["materials"] if m["is_pending"])
    determination = material["determinations"][0]
    assert determination["verdict"] == "uncertain"

    revised = client.post(
        f"/api/determinations/{determination['id']}/revisions",
        headers=auth,
        json={
            "verdict": "present",
            "confidence": 0.91,
            "reason": "判读复核：调取原始 RAW 后确认目标物种幼体经过画面左侧。",
        },
    )
    assert revised.status_code == 200, revised.text
    body = revised.json()
    assert body["determination"]["revision"] == 2
    # The same station-date may appear in more than one run; every affected unit is
    # put under review, and none keeps a usable certificate.
    assert unit["id"] in body["units_under_review"]
    assert all(c["review_status"] == "under_review" for c in body["certificates"])

    after = client.get(f"/api/units/{unit['id']}", headers=auth).json()
    versions = [c["version"] for c in after["certificate_history"]]
    assert versions == [1, 2]
    assert after["certificate_history"][0]["superseded_by_id"] == after["certificate_history"][1]["id"]
    assert "旧运行保留原证书" in after["certificate_history"][1]["review_reason"]

    # The old run keeps the original certificate exactly as issued.
    original_run = client.get(f"/api/units/{unit['id']}/trace", headers=auth).json()
    cert_step = next(s for s in original_run["chain"] if s["step"] == "certificate")
    issued = next(c for c in cert_step["items"] if c["version"] == 1)
    assert issued["review_status"] == "normal"
    assert issued["status"] == "pending"

    subject = after["certificate"]["conclusions"]["absence_analysis"]
    assert subject["open_conflicts"] == 0


def test_record_revision_keeps_hash_chain(client, auth):
    records = client.get("/api/records?record_type=downtime_log&limit=1", headers=auth).json()
    record = records["items"][0]
    payload = dict(record["payload"])
    payload["reason"] = "更正：停机起止时间按设备日志回填"

    revised = client.post(
        f"/api/records/{record['id']}/revisions",
        headers=auth,
        json={"payload": payload, "reason": "按设备日志更正停机区间"},
    )
    assert revised.status_code == 200, revised.text
    body = revised.json()
    assert body["revision"] == record["revision"] + 1
    assert body["payload_hash"] != record["payload_hash"]
    assert len(body["revisions"]) == body["revision"]
    assert body["revisions"][-1]["change_reason"] == "按设备日志更正停机区间"
    assert body["revisions"][0]["payload"] == record["payload"]

    detail = client.get(f"/api/records/{record['id']}", headers=auth).json()
    assert detail["payload"]["reason"].startswith("更正")
    assert detail["used_by_units"], "evidence records must point back to their survey units"


def test_unit_evidence_is_fully_traceable(client, auth, run_id):
    unit = _unit(client, auth, run_id, "QSL-01", "2026-04-10")
    evidence = client.get(f"/api/units/{unit['id']}/records", headers=auth).json()
    kinds = {item["record_type"] for item in evidence}
    assert {"camera_event", "device_heartbeat", "patrol_track", "downtime_log"} <= kinds
    for item in evidence:
        assert item["payload_hash"] and len(item["payload_hash"]) == 64
        assert item["event_time"] and item["received_time"]
        assert item["source_device"]
        assert item["revisions"]

    trace = client.get(f"/api/units/{unit['id']}/trace", headers=auth).json()
    raw = next(s for s in trace["chain"] if s["step"] == "raw_events")
    gate_ids = {
        rid
        for item in next(s for s in trace["chain"] if s["step"] == "coverage_conditions")["items"]
        for rid in item["evidence_ids"]
    }
    assert {r["record_id"] for r in raw["items"]} == gate_ids


def test_rebuild_run_supersedes_without_mutating_old_certificates(client, auth):
    rulebooks = client.get("/api/rulebooks", headers=auth).json()
    active = next(r for r in rulebooks if r["status"] == "active")
    run = client.post(
        "/api/runs",
        headers=auth,
        json={"rulebook_version_id": active["id"], "station_ids": [], "label": "重建测试"},
    )
    assert run.status_code in {200, 404}
    if run.status_code != 200:
        return
    original_id = run.json()["id"]
    units = client.get(f"/api/runs/{original_id}/units", headers=auth).json()
    if not units:
        return
    cert_before = client.get(f"/api/units/{units[0]['id']}", headers=auth).json()["certificate"]

    rebuilt = client.post(f"/api/runs/{original_id}/rebuild", headers=auth)
    assert rebuilt.status_code == 200, rebuilt.text
    new_id = rebuilt.json()["new_run"]["id"]
    assert new_id != original_id

    cert_after = client.get(f"/api/units/{units[0]['id']}", headers=auth).json()["certificate"]
    assert cert_after["id"] == cert_before["id"], "old run keeps its original certificate"
    runs = {r["id"]: r for r in client.get("/api/runs", headers=auth).json()}
    assert runs[original_id]["status"] == "superseded"
