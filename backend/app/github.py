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
