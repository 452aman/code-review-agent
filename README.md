# Autonomous Code Review Agent

A multi-agent code reviewer. Paste code or point it at a GitHub pull request and a LangGraph pipeline runs static analysis, retrieves relevant review guidance from a FAISS index, fans out to four specialist LLM agents in parallel, then **verifies** their output and **checks every severity against an explicit rubric** before ranking and scoring it.

**Stack:** FastAPI · LangGraph · Groq (LLM) · FAISS + fastembed (bge-small) · SQLAlchemy · SSE · React 19 · TypeScript · Monaco · TanStack Query · Tailwind · Recharts · Docker

## Pipeline

```
ingest → static analysis → FAISS retrieval ─┬─ security agent ───────┐
                                            ├─ bug agent ────────────┤
                                            ├─ performance agent ────┼→ verify & merge → summarize
                                            └─ maintainability agent ┘
                                         verify & merge → severity check → summarize
```

| Stage | What it does |
|---|---|
| **Static analysis** | AST checks for Python (cyclomatic complexity, mutable defaults, swallowed exceptions, nested loops) plus 17 regex rules across languages (secrets, SQL injection, `eval`, `shell=True`, unsafe deserialization, missing timeouts…). Deterministic, so the review still works with no API key. |
| **Retrieval (RAG)** | A curated 33-entry knowledge base (OWASP, Python pitfalls, performance, maintainability) is embedded with `bge-small-en-v1.5` and indexed in FAISS. Each agent receives the top matches for its own category, seeded with the static findings. |
| **Parallel agents** | Four LangGraph nodes run concurrently and return structured JSON (Pydantic schema: line, severity, explanation, suggestion, optional replacement code). Calls are concurrency-limited and retried with the server's `retry-after` hint on rate limits. If an agent fails, the review degrades instead of failing. |
| **Verifier** | Drops findings pointing at lines that don't exist or (for PRs) weren't changed, then merges duplicates reported by several agents using title overlap on nearby lines, keeping the highest severity and the richest explanation. The UI shows how many unsupported findings were rejected. |
| **Severity check** | A post-generation validation node (`services/severity.py`). LLMs are least reliable at *severity*: the same issue comes back "high" one run and "medium" the next, and a style nit can be called "critical". Every agent-assigned severity is checked against an explicit rule set that gives each kind of issue an allowed band (for example, `critical` is reachable only for exploitable flaws such as injection or leaked secrets, never for style or maintainability). A severity outside its band is clamped into it and the change is recorded in `stats.severity_calibration` with the reason. Static findings come from fixed rules and are never touched. Results are also reported in 3 tiers (high / medium / low). |
| **Determinism** | The four agents run in parallel and finish in any order, so their output is sorted before merging and every sort has a full tie-break. The same input therefore yields the same findings, order and score (covered by a test with randomised agent timing). The LLM runs at temperature 0. |
| **Scoring** | Severity-weighted penalty with exponential decay → 0-100 score and A-F grade. |

## Evaluation

Quality is measured, not asserted. `eval/cases.json` holds labelled code samples (what a correct review must find and the severity it should get, plus clean files that must produce nothing) and labelled questions for the FAISS rule corpus. `app/evaluation.py` scores the real pipeline against them:

```bash
cd backend
python -m app.evaluation          # readable report
python -m app.evaluation --json   # for CI;  POST /api/v1/evaluation/run does the same over HTTP
```

Latest run (deterministic stages only: static analysis, retrieval, verifier and severity check, no LLM, no network):

| Measure | Result |
|---|---|
| Labelled cases | 19 (14 detectable by the rules, 5 that need the LLM agents, 2 clean files) |
| Precision | 100% (0 false positives) |
| Recall, rule-detectable issues | 100% (12 of 12) |
| Recall, overall | 71% (12 of 17): the 5 misses are logic issues (IDOR, off-by-one, check-then-insert race, N+1 query, missing None check) that only the LLM agents can find |
| Severity correct on matched findings | 100% exact |
| Identical findings on repeat runs | 100% |
| Rule-corpus retrieval (FAISS, `bge-small`) | hit@1 91%, hit@3 100% over 11 questions (73% hit@3 with the offline hash embedder used in tests) |

Read these numbers with care: the cases are hand-written and small, and the detectable ones were written around the implemented rules, so this suite catches **regressions** and makes changes comparable. It is not a claim of real-world accuracy. The five agent-only cases are kept on purpose so the gap is visible; pass a real LLM to `evaluate()` to measure the agents.

## Scope and limits

- **Static, not executed.** Submitted code is parsed and read, never run, so there is no code-execution risk and no sandbox. Findings are suggestions for a human to triage.
- **A pipeline, not a single ReAct loop.** The graph is fixed (static analysis, retrieval, four parallel agents, verify, severity check, summarize), which keeps it predictable, testable and cheaper than a free-roaming agent.
- **Repeatable after generation, not before.** Everything downstream of the model is deterministic, but the model's own findings and wording can still differ between runs.

## Product features

- **Live pipeline view**: server-sent events stream each node's progress; late subscribers replay from persisted history.
- **Monaco code viewer** with severity-coloured line highlights, click-to-jump from any finding.
- **Triage**: accept or dismiss findings; dismissed ones disappear from the code view.
- **GitHub integration**: review a PR by URL (only changed lines are in scope; line numbers map to the real file), HMAC-verified `pull_request` webhook, and optional posting of findings back to the PR as inline review comments.
- **Quality gate**: every review gets a pass/fail you can wire into CI. Defaults fail on any *critical* finding; tune it per request (`GET /api/v1/reviews/{id}/gate?fail_on=critical,high&min_score=70`) or per repo. For PRs it is also posted as a GitHub commit status (`code-review-agent/quality-gate`), so branch protection can require it.
- **Only new issues**: each review is compared with the previous review of the same PR (or same title for pasted code). Findings are matched by a fingerprint of *the flagged line of code*, not its line number or the LLM's wording, so moving code around doesn't resurface old issues. Each finding is marked new or existing, and issues that disappeared are counted as fixed. PR comments are posted for new findings only.
- **Applyable fixes**: where a fix is known, PR comments carry a GitHub *suggestion* block (one-click apply); pasted-code reviews can download a `git apply`-able patch (`/patch.diff`); every review exports **SARIF 2.1.0** (`/sarif`) for GitHub code scanning.
- **Per-repo rules** (`.codereview.yml`): ignore rules or paths, re-grade severities, and set the gate. Read automatically from the PR's head commit; invalid or missing files never block a review.
- **Crash-safe jobs**: sources are stored with the review, so interrupted reviews resume after a restart and any review can be re-run.
- **Production plumbing**: request IDs, structured JSON logs, `/metrics`, `/ready`, security headers, a rate limit on review creation (protects the LLM budget), and GitHub Actions CI.
- **Dashboard**: score trend, severity mix and most common issues over time.
- **Rules explorer**: semantic search over the knowledge base.

## Run locally

```bash
cd backend
python3 -m venv code_review_agent_env && code_review_agent_env/bin/python3 -m pip install -r requirements.txt
cp .env.example .env            # add GROQ_API_KEY (works without it, static analysis only)
code_review_agent_env/bin/uvicorn app.main:app --port 8001 --reload

cd ../frontend && npm install && npm run dev    # http://localhost:5180
```

`docker compose up --build` serves everything on http://localhost:8081.

Tests (no network or API key needed; fake LLM and offline embedder; 29 tests covering the gate, baselines, patch/SARIF exports, recovery and GitHub payloads): `cd backend && code_review_agent_env/bin/python3 -m pytest`

## Configuration

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | Enables the four LLM agents and the summary. |
| `LLM_MODEL` | Default `openai/gpt-oss-120b`. |
| `GITHUB_TOKEN` | Private PRs, and posting review comments. |
| `GITHUB_WEBHOOK_SECRET` | Enables `POST /api/v1/webhooks/github`. |
| `REVIEW_RATE_LIMIT` | Reviews per client per minute (default `30/minute`). |

### Using the gate in CI

```yaml
- run: |
    ID=$(curl -s -X POST $REVIEW_URL/api/v1/reviews -H 'content-type: application/json' -d "$(jq -Rs '{files:[{path:"app.py",content:.}]}' app.py)" | jq -r .id)
    until [ "$(curl -s $REVIEW_URL/api/v1/reviews/$ID | jq -r .status)" = done ]; do sleep 3; done
    curl -s "$REVIEW_URL/api/v1/reviews/$ID/gate" | tee gate.json; jq -e .passed gate.json
```

Note: this service has no user accounts. Put it behind your own auth/network boundary if exposed publicly.
