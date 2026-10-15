import hashlib
import hmac
import re

import httpx

from app.core.config import get_settings

PR_URL = re.compile(r"github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/pull/(?P<num>\d+)")
API = "https://api.github.com"


class GitHubError(Exception):
    pass


def parse_pr_url(url: str) -> tuple[str, str, int]:
    m = PR_URL.search(url)
    if not m:
        raise GitHubError("Not a GitHub pull request URL")
    return m["owner"], m["repo"], int(m["num"])


def _headers(accept: str) -> dict:
    h = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "code-review-agent"}
    if token := get_settings().github_token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def fetch_pr(owner: str, repo: str, num: int, client: httpx.AsyncClient | None = None) -> tuple[str, dict]:
    """Return (unified diff, pull request metadata)."""
    async with (client or httpx.AsyncClient(timeout=20)) as c:
        meta = await c.get(f"{API}/repos/{owner}/{repo}/pulls/{num}", headers=_headers("application/vnd.github+json"))
        if meta.status_code == 404:
            raise GitHubError("Pull request not found (or private: set GITHUB_TOKEN)")
        if meta.status_code >= 400:
            raise GitHubError(f"GitHub returned {meta.status_code}")
        diff = await c.get(f"{API}/repos/{owner}/{repo}/pulls/{num}", headers=_headers("application/vnd.github.v3.diff"))
        if diff.status_code >= 400:
            raise GitHubError(f"GitHub returned {diff.status_code} fetching diff")
        return diff.text, meta.json()


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


async def post_review(owner: str, repo: str, num: int, head_sha: str, summary: str, comments: list[dict],
                      client: httpx.AsyncClient | None = None) -> None:
    """Post findings as a single PR review with inline comments. Needs GITHUB_TOKEN with write access."""
    if not get_settings().github_token:
        return
    body = {"commit_id": head_sha, "event": "COMMENT", "body": summary, "comments": comments[:30]}
    async with (client or httpx.AsyncClient(timeout=20)) as c:
        r = await c.post(f"{API}/repos/{owner}/{repo}/pulls/{num}/reviews", json=body,
                         headers=_headers("application/vnd.github+json"))
        if r.status_code >= 400:
            raise GitHubError(f"Could not post review: {r.status_code} {r.text[:200]}")


async def post_status(owner: str, repo: str, sha: str, state: str, description: str, target_url: str | None = None,
                      client: httpx.AsyncClient | None = None) -> None:
    """Report the quality gate as a commit status ("success" / "failure"), so branch protection can require it."""
    if not get_settings().github_token:
        return
    body = {"state": state, "description": description[:140], "context": "code-review-agent/quality-gate"}
    if target_url:
        body["target_url"] = target_url
    async with (client or httpx.AsyncClient(timeout=20)) as c:
        r = await c.post(f"{API}/repos/{owner}/{repo}/statuses/{sha}", json=body, headers=_headers("application/vnd.github+json"))
        if r.status_code >= 400:
            raise GitHubError(f"Could not set status: {r.status_code} {r.text[:200]}")


async def fetch_text_file(owner: str, repo: str, path: str, ref: str, client: httpx.AsyncClient | None = None) -> str | None:
    """Fetch one file at a commit (used for .codereview.yml). Returns None when it does not exist."""
    async with (client or httpx.AsyncClient(timeout=20)) as c:
        r = await c.get(f"{API}/repos/{owner}/{repo}/contents/{path}", params={"ref": ref}, headers=_headers("application/vnd.github.raw+json"))
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise GitHubError(f"GitHub returned {r.status_code} fetching {path}")
        return r.text


def review_comment(f) -> str:
    """Inline comment body. When a fix is known it is emitted as a GitHub suggestion block (one-click apply)."""
    body = f"**{f.severity.value.upper()}** · {f.title}\n\n{f.explanation}\n\n_Suggestion:_ {f.suggestion}"
    if f.fix and "\n" not in f.fix.strip():
        body += f"\n\n```suggestion\n{f.fix.strip()}\n```"
    return body
