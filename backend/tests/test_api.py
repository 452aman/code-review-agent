import hashlib
import hmac
import json
import time

from app.analysis.diff import parse_diff
from app.analysis.static import analyze
from app.core.types import SourceFile

CODE = 'import os\n\nAPI_KEY = "sk_live_abcdef123456"\nx = eval(input())\n'


def wait_done(client, rid, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(f"/api/v1/reviews/{rid}").json()
        if r["status"] in ("done", "failed"):
            return r
        time.sleep(0.1)
    raise AssertionError("review did not finish")


def test_static_review_end_to_end(client):
    r = client.post("/api/v1/reviews", json={"files": [{"path": "app.py", "content": CODE}]})
    assert r.status_code == 202
    done = wait_done(client, r.json()["id"])
    assert done["status"] == "done"
    titles = {f["title"] for f in done["findings"]}
    assert "Hardcoded credential" in titles and "Use of eval()" in titles
    assert done["score"] < 100 and done["grade"] in "ABCDF"
    assert [f["severity"] for f in done["findings"]][0] == "critical"  # ranked most severe first
    assert any(e["node"] == "static_analysis" for e in done["events"])


def test_agent_findings_merge_and_hallucinations_dropped(client, fake_llm):
    rid = client.post("/api/v1/reviews", json={"files": [{"path": "app.py", "content": CODE}]}).json()["id"]
    done = wait_done(client, rid)
    assert done["stats"]["rejected_hallucinations"] == 1
    evals = [f for f in done["findings"] if f["line"] == 4]
    assert len(evals) == 1 and evals[0]["fix"] == "x = json.loads(s)"  # agent fix merged onto the static finding
    assert done["summary"] == "Fake summary."


def test_triage_and_sse_replay(client):
    rid = client.post("/api/v1/reviews", json={"files": [{"path": "app.py", "content": CODE}]}).json()["id"]
    done = wait_done(client, rid)
    fid = done["findings"][0]["id"]
    assert client.patch(f"/api/v1/findings/{fid}", json={"triage": "dismissed"}).json()["triage"] == "dismissed"
    assert client.patch(f"/api/v1/findings/{fid}", json={"triage": "bogus"}).status_code == 422
    with client.stream("GET", f"/api/v1/reviews/{rid}/events") as s:
        body = "".join(s.iter_text())
    assert "static_analysis" in body and "complete" in body


def test_request_validation(client):
    assert client.post("/api/v1/reviews", json={}).status_code == 422
    assert client.post("/api/v1/reviews", json={"files": [{"path": "a.py", "content": "x"}], "pr_url": "u"}).status_code == 422
    assert client.post("/api/v1/reviews", json={"pr_url": "https://example.com/nope"}).status_code == 400
    assert client.get("/api/v1/reviews/nope").status_code == 404


def test_rules_search_and_stats(client):
    assert len(client.get("/api/v1/rules/search", params={"q": "sql injection query", "k": 3}).json()) == 3
    assert "reviews" in client.get("/api/v1/stats").json()


DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -10,3 +10,4 @@ def f():
     a = 1
-    b = 2
+    b = eval(x)
+    c = 3
     return a
"""


def test_diff_parser_tracks_changed_lines_and_scopes_findings():
    files = parse_diff(DIFF)
    assert files[0].path == "app.py" and files[0].changed_lines == [11, 12]
    assert files[0].content.splitlines()[10].strip() == "b = eval(x)"  # line numbers match the real file
    assert [f.line for f in analyze(files[0])] == [11]  # untouched lines are out of scope


def test_webhook_requires_valid_signature(client, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "github_webhook_secret", "s3cret")
    body = json.dumps({"action": "closed"}).encode()
    assert client.post("/api/v1/webhooks/github", content=body, headers={"X-Hub-Signature-256": "sha256=bad"}).status_code == 401
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    r = client.post("/api/v1/webhooks/github", content=body, headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "pull_request"})
    assert r.status_code == 202 and r.json() == {"ignored": "action"}
