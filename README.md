# Autonomous Code Review Agent

A multi-agent code reviewer. Paste code or point it at a GitHub pull request and a LangGraph pipeline runs static analysis, retrieves relevant review guidance from a FAISS index, fans out to four specialist LLM agents in parallel, then **verifies** their output before ranking and scoring it.

**Stack:** FastAPI · LangGraph · Groq (LLM) · FAISS + fastembed (bge-small) · SQLAlchemy · SSE · React 19 · TypeScript · Monaco · TanStack Query · Tailwind · Recharts · Docker

## Pipeline

```
ingest → static analysis → FAISS retrieval ─┬─ security agent ───────┐
                                            ├─ bug agent ────────────┤
                                            ├─ performance agent ────┼→ verify & merge → summarize
                                            └─ maintainability agent ┘
```

| Stage | What it does |
|---|---|
| **Static analysis** | AST checks for Python (cyclomatic complexity, mutable defaults, swallowed exceptions, nested loops) plus 17 regex rules across languages (secrets, SQL injection, `eval`, `shell=True`, unsafe deserialization, missing timeouts…). Deterministic, so the review still works with no API key. |
| **Retrieval (RAG)** | A curated 33-entry knowledge base (OWASP, Python pitfalls, performance, maintainability) is embedded with `bge-small-en-v1.5` and indexed in FAISS. Each agent receives the top matches for its own category, seeded with the static findings. |
| **Parallel agents** | Four LangGraph nodes run concurrently and return structured JSON (Pydantic schema: line, severity, explanation, suggestion, optional replacement code). Calls are concurrency-limited and retried with the server's `retry-after` hint on rate limits. If an agent fails, the review degrades instead of failing. |
| **Verifier** | Drops findings pointing at lines that don't exist or (for PRs) weren't changed, then merges duplicates reported by several agents using title overlap on nearby lines, keeping the highest severity and the richest explanation. The UI shows how many unsupported findings were rejected. |
| **Scoring** | Severity-weighted penalty with exponential decay → 0-100 score and A-F grade. |

## Product features

- **Live pipeline view**: server-sent events stream each node's progress; late subscribers replay from persisted history.
- **Monaco code viewer** with severity-coloured line highlights, click-to-jump from any finding.
- **Triage**: accept or dismiss findings; dismissed ones disappear from the code view.
- **GitHub integration**: review a PR by URL (only changed lines are in scope; line numbers map to the real file), HMAC-verified `pull_request` webhook, and optional posting of findings back to the PR as inline review comments.
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

Tests (no network or API key needed; uses a fake LLM and an offline embedder): `cd backend && code_review_agent_env/bin/python3 -m pytest`

## Configuration

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | Enables the four LLM agents and the summary. |
| `LLM_MODEL` | Default `openai/gpt-oss-120b`. |
| `GITHUB_TOKEN` | Private PRs, and posting review comments. |
| `GITHUB_WEBHOOK_SECRET` | Enables `POST /api/v1/webhooks/github`. |
