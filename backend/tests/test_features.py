import asyncio
import json
import time

import httpx
import pytest

from app import github, runner
from app.api import routes
from app.core.types import Finding, Severity
from app.db.models import FindingRow, Review, SessionLocal
from app.services.config import ConfigError, ReviewConfig, apply_config, parse_config
from app.services.exports import build_patch
from app.services.fingerprint import fingerprint

V1 = 'import os\n\nAPI_KEY = "sk_live_abcdef123456"\nx = eval(input())\n'
V2_FIXED_EVAL_ADDED_EXEC = '\n\n\nimport os\n\nAPI_KEY = "sk_live_abcdef123456"\nexec(open("a").read())\n'   # line numbers shifted, eval gone, exec new


def run_review(client, content, path="app.py", title="app.py", **extra):
    r = client.post("/api/v1/reviews", json={"title": title, "files": [{"path": path, "content": content}], **extra})
    assert r.status_code == 202, r.text
    rid = r.json()["id"]
    end = time.time() + 20
    while time.time() < end:
        d = client.get(f"/api/v1/reviews/{rid}").json()
        if d["status"] in ("done", "failed"):
            return d
        time.sleep(0.1)
    raise AssertionError("review did not finish")


# ------------------------------------------------------------------------------------------- repo config
def test_config_parsing_and_validation():
    cfg = parse_config("ignore_rules: [MNT002]\nignore_paths: ['tests/**']\nseverity_overrides: {SEC011: low}\ngate: {fail_on: [critical, high], min_score: 60}")
    assert cfg.ignore_rules == ["MNT002"] and cfg.gate.min_score == 60 and cfg.severity_overrides["SEC011"] == Severity.low
    for bad in ("ignore_rules: 5", "severity_overrides: {SEC011: catastrophic}", "- just\n- a list", "gate: {min_score: 500}", "a: [unclosed"):
        with pytest.raises(ConfigError):
            parse_config(bad)
    assert parse_config("").gate.fail_on == [Severity.critical]            # empty file = defaults


def test_apply_config_ignores_rules_and_paths_and_regrades():
    mk = lambda rid, file, sev: Finding(file=file, line=1, severity=sev, category="security", title="t", rule_id=rid)  # noqa: E731
    kept, n = apply_config([mk("A", "src/a.py", Severity.high), mk("B", "tests/test_a.py", Severity.high), mk("C", "src/c.py", Severity.high)],
                           ReviewConfig(ignore_rules=["A"], ignore_paths=["tests/**"], severity_overrides={"C": Severity.low}))
    assert n == 2 and [f.severity for f in kept] == [Severity.low]


def test_review_honours_a_pasted_config_and_rejects_a_bad_one(client):
    plain = run_review(client, V1)
    assert {f["rule_id"] for f in plain["findings"]} >= {"SEC001", "SEC004"}
    cfg = run_review(client, V1, title="other", config="ignore_rules: [SEC001]\nseverity_overrides: {SEC004: low}")
    ids = {f["rule_id"]: f for f in cfg["findings"]}
    assert "SEC001" not in ids and ids["SEC004"]["severity"] == "low"
    assert cfg["stats"]["suppressed_by_config"] == 1 and cfg["score"] > plain["score"]
    assert client.post("/api/v1/reviews", json={"files": [{"path": "a.py", "content": "x=1"}], "config": "severity_overrides: {X: nope}"}).status_code == 422


# ------------------------------------------------------------------------------------------- baseline (only new issues)
def test_fingerprint_ignores_line_numbers_and_wording_but_not_the_code():
    a = fingerprint("a.py", "security", "x = eval(input())", "SEC004")
    assert a == fingerprint("a.py", "security", "   x  =  eval(input())  ", "SEC004")
    assert a != fingerprint("a.py", "security", "y = eval(input())", "SEC004") and a != fingerprint("b.py", "security", "x = eval(input())", "SEC004")


def test_second_review_marks_new_existing_and_fixed_even_when_lines_shift(client):
    first = run_review(client, V1)
    assert first["baseline_id"] is None and all(f["is_new"] for f in first["findings"])
    second = run_review(client, V2_FIXED_EVAL_ADDED_EXEC)
    assert second["baseline_id"] == first["id"]
    by_rule = {f["rule_id"]: f for f in second["findings"]}
    assert by_rule["SEC001"]["is_new"] is False and by_rule["SEC001"]["line"] != 3      # same secret, different line, recognised
    assert by_rule["SEC005"]["is_new"] is True                                           # the new exec()
    assert second["stats"]["new"] == 1 and second["stats"]["existing"] == 1 and second["stats"]["fixed"] == 1   # eval was removed


# ------------------------------------------------------------------------------------------- quality gate
def test_gate_fails_on_critical_and_passes_when_clean(client):
    bad = run_review(client, V1)
    g = client.get(f"/api/v1/reviews/{bad['id']}/gate").json()
    assert g["passed"] is False and "critical" in g["reasons"][0] and bad["gate"]["passed"] is False
    # raising the bar to "high" still fails (the hard-coded secret is critical, and exec/eval are high); an unrelated level passes
    assert client.get(f"/api/v1/reviews/{bad['id']}/gate", params={"fail_on": "high"}).json()["passed"] is False
    assert client.get(f"/api/v1/reviews/{bad['id']}/gate", params={"fail_on": "info"}).json()["passed"] is True
    clean = run_review(client, "def add(a: int, b: int) -> int:\n    return a + b\n", title="clean")
    assert client.get(f"/api/v1/reviews/{clean['id']}/gate").json()["passed"] is True


def test_gate_thresholds_overrides_and_dismissals(client):
    r = run_review(client, V1)
    gid = r["id"]
    assert client.get(f"/api/v1/reviews/{gid}/gate", params={"fail_on": "info"}).json()["passed"] is True       # nothing at info level
    assert client.get(f"/api/v1/reviews/{gid}/gate", params={"min_score": 95, "fail_on": "info"}).json()["passed"] is False
    assert client.get(f"/api/v1/reviews/{gid}/gate", params={"fail_on": "bogus"}).status_code == 422
    for f in r["findings"]:                                                                                        # triage everything away
        client.patch(f"/api/v1/findings/{f['id']}", json={"triage": "dismissed"})
    assert client.get(f"/api/v1/reviews/{gid}/gate").json()["passed"] is True


def test_gate_with_baseline_only_counts_new_findings(client):
    run_review(client, V1)
    second = run_review(client, V2_FIXED_EVAL_ADDED_EXEC)                    # critical secret is old, exec() is new but only "high"
    assert client.get(f"/api/v1/reviews/{second['id']}/gate").json()["passed"] is True
    assert client.get(f"/api/v1/reviews/{second['id']}/gate", params={"only_new": "false"}).json()["passed"] is False
    assert client.get(f"/api/v1/reviews/{second['id']}/gate", params={"fail_on": "high"}).json()["passed"] is False


def test_gate_needs_a_finished_review(client):
    assert client.get("/api/v1/reviews/nope/gate").status_code == 404
    with SessionLocal() as db:
        r = Review(title="t", source="paste", status="running", files=[])
        db.add(r)
        db.commit()
        rid = r.id
    assert client.get(f"/api/v1/reviews/{rid}/gate").status_code == 409


# ------------------------------------------------------------------------------------------- exports
def test_patch_applies_suggested_fix_with_indentation_and_unified_format(client, fake_llm):
    d = run_review(client, "def f(s):\n    x = eval(s)\n    return x\n")        # the fake security agent only proposes a fix for line 4, which this file lacks
    assert client.get(f"/api/v1/reviews/{d['id']}/patch.diff").status_code == 404
    code = "import os\n\nAPI_KEY = 1\nx = eval(input())\n"                      # line 4 matches the fake agent's finding
    d = run_review(client, code, title="p")
    r = client.get(f"/api/v1/reviews/{d['id']}/patch.diff")
    assert r.status_code == 200 and r.headers["x-fixes-applied"] == "1"
    assert "--- a/app.py" in r.text and "-x = eval(input())" in r.text and "+x = json.loads(s)" in r.text


def test_patch_helper_keeps_indentation_and_handles_overlaps():
    f = lambda line, fix: FindingRow(file="a.py", line=line, fix=fix, triage="open")  # noqa: E731
    diff, n = build_patch([{"path": "a.py", "content": "def f():\n    a = 1\n    b = 2\n"}], [f(2, "a = 10"), f(2, "a = 11"), f(3, "b = 20")])
    assert n == 2                                                              # two fixes on the same line: only the first is applied
    assert "+    a = 10" in diff and "a = 11" not in diff and "+    b = 20" in diff
    assert build_patch([{"path": "a.py", "content": "x=1\n"}], [FindingRow(file="a.py", line=1, fix="y=2", triage="dismissed")]) == ("", 0)


def test_patch_not_offered_for_pull_requests_or_without_fixes(client):
    d = run_review(client, V1)
    assert client.get(f"/api/v1/reviews/{d['id']}/patch.diff").status_code == 404            # no LLM, so no automatic fixes
    with SessionLocal() as db:
        r = Review(title="pr", source="github_pr", status="done", files=[], stats={})
        db.add(r)
        db.commit()
        rid = r.id
    assert client.get(f"/api/v1/reviews/{rid}/patch.diff").status_code == 409


def test_sarif_is_valid_and_skips_dismissed_findings(client):
    d = run_review(client, V1)
    sarif = client.get(f"/api/v1/reviews/{d['id']}/sarif").json()
    assert sarif["version"] == "2.1.0" and sarif["runs"][0]["tool"]["driver"]["name"] == "code-review-agent"
    res = {x["ruleId"]: x for x in sarif["runs"][0]["results"]}
    assert res["SEC001"]["level"] == "error" and res["SEC001"]["locations"][0]["physicalLocation"]["region"]["startLine"] == 3
    assert res["SEC001"]["partialFingerprints"]["primaryLocationLineHash"]
    assert {r["id"] for r in sarif["runs"][0]["tool"]["driver"]["rules"]} >= {"SEC001", "SEC004"}
    fid = next(f["id"] for f in d["findings"] if f["rule_id"] == "SEC004")
    client.patch(f"/api/v1/findings/{fid}", json={"triage": "dismissed"})
    assert "SEC004" not in {x["ruleId"] for x in client.get(f"/api/v1/reviews/{d['id']}/sarif").json()["runs"][0]["results"]}


# ------------------------------------------------------------------------------------------- rerun + recovery
def test_rerun_restores_a_failed_review_from_its_stored_files(client):
    d = run_review(client, V1)
    with SessionLocal() as db:
        r = db.get(Review, d["id"])
        r.status, r.error = "failed", "LLM exploded"
        db.commit()
    assert client.post(f"/api/v1/reviews/{d['id']}/rerun").status_code == 202
    end = time.time() + 20
    while time.time() < end and client.get(f"/api/v1/reviews/{d['id']}").json()["status"] != "done":
        time.sleep(0.1)
    again = client.get(f"/api/v1/reviews/{d['id']}").json()
    assert again["status"] == "done" and again["error"] is None and len(again["findings"]) == len(d["findings"])   # no duplicated findings
    assert client.post("/api/v1/reviews/missing/rerun").status_code == 404


def test_reviews_interrupted_by_a_restart_are_resumed(client):
    from fastapi.testclient import TestClient
    from app.main import app

    with SessionLocal() as db:
        r = Review(title="stuck", source="paste", status="running", files=[{"path": "a.py", "content": V1, "language": "python", "changed_lines": None}])
        db.add(r)
        db.commit()
        rid = r.id
    with TestClient(app) as restarted:                                           # startup recovery runs here
        end = time.time() + 20
        while time.time() < end and restarted.get(f"/api/v1/reviews/{rid}").json()["status"] != "done":
            time.sleep(0.1)
        assert restarted.get(f"/api/v1/reviews/{rid}").json()["status"] == "done"


# ------------------------------------------------------------------------------------------- GitHub integration
def _mock(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_commit_status_payload(monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "github_token", "t")
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["body"] = str(req.url), json.loads(req.content)
        return httpx.Response(201, json={})

    asyncio.run(github.post_status("o", "r", "abc123", "failure", "x" * 300, client=_mock(handler)))
    assert seen["url"].endswith("/repos/o/r/statuses/abc123")
    assert seen["body"]["state"] == "failure" and len(seen["body"]["description"]) == 140 and seen["body"]["context"] == "code-review-agent/quality-gate"


def test_status_not_sent_without_token(monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "github_token", None)
    called = []
    asyncio.run(github.post_status("o", "r", "sha", "success", "ok", client=_mock(lambda r: called.append(r) or httpx.Response(201))))
    assert called == []


def test_repo_config_fetch_and_graceful_failures(monkeypatch):
    asyncio.run(_repo_cfg_cases(monkeypatch))


async def _repo_cfg_cases(monkeypatch):
    async def fake_ok(*a, **k): return "ignore_rules: [MNT002]\n"
    async def fake_missing(*a, **k): return None
    async def fake_bad(*a, **k): return "severity_overrides: {X: nope}"
    async def fake_down(*a, **k): raise github.GitHubError("GitHub returned 500")
    for fn, expected in ((fake_ok, ["MNT002"]), (fake_missing, None), (fake_bad, None), (fake_down, None)):
        monkeypatch.setattr(github, "fetch_text_file", fn)
        cfg = await routes._repo_config("o", "r", "sha")
        assert (cfg.ignore_rules if cfg else None) == expected                       # a bad or missing file never blocks a review


def test_fetch_text_file_handles_missing_file():
    assert asyncio.run(github.fetch_text_file("o", "r", ".codereview.yml", "sha", client=_mock(lambda r: httpx.Response(404)))) is None
    assert asyncio.run(github.fetch_text_file("o", "r", ".codereview.yml", "sha", client=_mock(lambda r: httpx.Response(200, text="a: 1")))) == "a: 1"


def test_inline_comment_uses_a_suggestion_block_only_for_single_line_fixes():
    f = Finding(file="a.py", line=1, severity=Severity.high, category="security", title="eval", explanation="e", suggestion="s", fix="x = json.loads(s)")
    assert "```suggestion\nx = json.loads(s)\n```" in github.review_comment(f)
    assert "```suggestion" not in github.review_comment(f.model_copy(update={"fix": "a = 1\nb = 2"}))
    assert "```suggestion" not in github.review_comment(f.model_copy(update={"fix": None}))


# ------------------------------------------------------------------------------------------- production plumbing
def test_request_id_security_headers_metrics_and_readiness(client):
    r = client.get("/health")
    assert len(r.headers["x-request-id"]) >= 8 and r.headers["x-frame-options"] == "DENY"
    assert client.get("/health", headers={"X-Request-ID": "trace-9"}).headers["x-request-id"] == "trace-9"
    run_review(client, V1)
    m = client.get("/metrics").text
    assert 'http_requests_total{method="POST",route="/api/v1/reviews",status="202"}' in m and "reviews_completed_total" in m and "quality_gate_failures_total" in m
    assert client.get("/ready").json() == {"status": "ready"}


def test_review_creation_is_rate_limited(client):
    codes = [client.post("/api/v1/reviews", json={"files": [{"path": "a.py", "content": "x = 1"}]}).status_code for _ in range(14)]
    assert codes[:12] == [202] * 12 and 429 in codes[12:]


# ------------------------------------------------------------------------------------------- upgrading an old database
LEGACY_DDL = [
    "CREATE TABLE reviews (id VARCHAR(32) PRIMARY KEY, title VARCHAR(200), source VARCHAR(20), source_ref VARCHAR(300), status VARCHAR(20), score INTEGER, grade VARCHAR(2),"
    " summary TEXT, stats JSON, files JSON, events JSON, error TEXT, duration_s FLOAT, created_at DATETIME)",
    "CREATE TABLE findings (id INTEGER PRIMARY KEY, review_id VARCHAR(32), file VARCHAR(300), line INTEGER, end_line INTEGER, severity VARCHAR(10), category VARCHAR(20),"
    " title VARCHAR(160), explanation TEXT, suggestion TEXT, fix TEXT, rule_id VARCHAR(20), source VARCHAR(30), confidence FLOAT, triage VARCHAR(12))",
    "INSERT INTO reviews VALUES ('legacy1', 'old review', 'paste', NULL, 'done', 70, 'C', 'old summary', '{}', '[]', '[]', NULL, 1.5, '2026-09-01 10:00:00')",
    "INSERT INTO findings VALUES (1, 'legacy1', 'a.py', 3, NULL, 'high', 'security', 'Use of eval()', '', '', NULL, 'SEC004', 'static', 0.8, 'open')",
]


def test_a_database_from_before_the_upgrade_is_migrated_and_still_serves(tmp_path):
    """The real upgrade path: ALTER TABLE ADD COLUMN leaves NULLs in old rows, and the API models must cope with them."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app.db.models import Review, ensure_schema
    from app.schemas import ReviewOut, ReviewSummary

    eng = create_engine(f"sqlite:///{tmp_path}/legacy.db")
    with eng.begin() as conn:
        for stmt in LEGACY_DDL:
            conn.execute(text(stmt))
    ensure_schema(eng)
    ensure_schema(eng)                                                       # running it twice is harmless
    with eng.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(reviews)"))}
        assert {"baseline_id", "config", "gate", "meta"} <= cols
        assert conn.execute(text("SELECT count(*) FROM reviews WHERE config IS NULL OR gate IS NULL OR meta IS NULL")).scalar() == 0   # backfilled
        assert conn.execute(text("SELECT count(*) FROM findings WHERE is_new IS NULL")).scalar() == 0
    with Session(eng) as db:
        r = db.get(Review, "legacy1")
        out = ReviewOut.model_validate(r)
        assert out.gate == {} and out.config == {} and out.findings[0].is_new is True and out.findings[0].rule_id == "SEC004"
        with eng.begin() as conn:                                            # even if a NULL slips through, the models tolerate it
            conn.execute(text("UPDATE reviews SET config = NULL, gate = NULL"))
            conn.execute(text("UPDATE findings SET is_new = NULL"))
        db.expire_all()
        r = db.get(Review, "legacy1")
        assert ReviewSummary.model_validate(r).gate == {} and ReviewOut.model_validate(r).findings[0].is_new is True
