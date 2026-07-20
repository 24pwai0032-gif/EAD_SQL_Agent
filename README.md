# EAD SQL Agent

Text-to-SQL agent with conditional visualisation for the **Economic Affairs
Division (EAD)**, Ministry of Economic Affairs, Government of Pakistan.

It answers natural-language questions about **Foreign Economic Assistance** —
external commitments, disbursements and debt service from foreign governments
and multilateral agencies — over a synthetic but realistic SQLite dataset.
This is *not* a domestic budget (PSDP) system; it models foreign assistance
flows only.

Architecture follows the LangChain SQL-agent pattern
(`create_agent` + `SQLDatabaseToolkit`):
https://docs.langchain.com/oss/python/langchain/sql-agent

## Quickstart

Requires Python 3.11 or newer.

```bash
pip install -r requirements.txt
cp .env.example .env            # add your OPENAI_API_KEY for development

python data/generate_dataset.py # builds data/ead.db (5000 rows, seeded)
pytest                          # deterministic suite: dataset, guard, charts
python eval/run_eval.py         # execution accuracy + chart appropriateness
uvicorn api.main:app            # POST /ask, GET /health (binds 127.0.0.1)
python ui/app.py                # Gradio testing UI (share=False, local only)
```

## Local deployment path (NTC) — and the OpenAI-for-testing caveat

Final deployment is on a local government server (NTC), fully on-premise,
with a locally hosted model behind an OpenAI-compatible endpoint (vLLM /
Ollama / TGI). **The OpenAI API is used for development and testing only** —
no production data ever goes to it, and nothing in the codebase depends on
the provider except one file.

`agent/llm.py` is the **only** place a model provider is named. To move to
NTC, edit `.env` only:

```
LLM_PROVIDER=local
LOCAL_BASE_URL=http://<ntc-host>:8000/v1
LOCAL_MODEL=qwen2.5-coder-32b-instruct
LOCAL_API_KEY=not-needed
```

No other file changes. At startup `verify_tool_calling()` binds a trivial
tool and fails loudly if the configured model cannot call tools — without
tool calling the agent cannot work at all, so this is checked before
anything is served.

## Safety

- **Read-only, twice.** SQLite is opened `mode=ro` (in production: a
  SELECT-only Postgres user), *and* every generated query is parsed with
  `sqlglot` before execution — single `SELECT` only; INSERT/UPDATE/DELETE/
  DROP/ALTER/ATTACH/PRAGMA and multi-statement queries are rejected; a
  `LIMIT` is injected when absent. Both layers are covered by tests.
- **Views only.** The agent sees `v_fea_transactions`, `v_commitments`,
  `v_disbursements`, `v_debt_service` — never base tables.
- **Statement timeout and row cap** on every query.
- The API binds `127.0.0.1` only; a gateway layer owns auth in production.
- The Gradio UI is for local testing and always runs `share=False` — on a
  government network `share=True` would tunnel out to gradio.live.
- No API key is ever hardcoded, printed, or logged.

## Conditional visualisation

The model *proposes* a chart via the `plot_result` tool (a specification,
never a rendered image); `agent/charts.py` *disposes* — hard rules validate
the spec against the actual result set: no charts for single values, bare
lists, fewer than 2 or more than 25 rows, or non-numeric results.
Inappropriate specs are dropped silently. Rendering happens in the client
(the Gradio UI uses matplotlib with the Agg backend; the API returns only
data + spec, keeping charts small over the wire and CDN-free for
air-gapped deployment).

## Response contract

`POST /ask` with `{"question": ..., "thread_id": ...}` returns:

```json
{
  "answer": "...",
  "sql": "SELECT ...",
  "columns": ["fiscal_year", "disbursed_usd_mn"],
  "rows": [["FY2016", 123.45]],
  "chart": {"type": "line", "x": "fiscal_year", "y": "disbursed_usd_mn", "title": "..."},
  "trace": ["sql_db_list_tables()", "sql_db_schema(...)", "sql_db_query(...)"]
}
```

`chart` is `null` whenever a chart would not help. Conversation memory is
per `thread_id`, so follow-ups like "and what about FY2023?" resolve.

The result table is captured from the very tool execution the model saw —
the SQL is never re-executed to build it.

## Evaluation

`eval/gold.json` holds 34 question/SQL pairs (including trap cases for
commitment-vs-disbursement ambiguity, NULL interest rates, fiscal vs
calendar years, mixed exchange rates, federal-NULL provinces, and
must-not-chart results). `eval/run_eval.py` scores **execution accuracy**
(result-set comparison, float tolerance 0.01 — not SQL string similarity)
and **chart appropriateness**. `--check-gold` verifies the gold SQL runs
without needing a model.

## Dataset

`data/generate_dataset.py` rebuilds `data/ead.db` from scratch on every run
(seeded, so the eval set stays stable): 5000 transactions across FY2016–
FY2026, 22 development partners mapped to 9 EAD wings, realistic scale by
partner type (UN agencies do not lend billions), rupee depreciation from
~105 to ~285 PKR/USD, NULL-means-something semantics (NULL interest = not a
loan; NULL province = federal project), and ~8% Pending/Rejected rows with
remarks. Verification checks print on every run.

## Layout

```
├── config.py               # env-driven settings (no provider named here)
├── data/generate_dataset.py
├── agent/
│   ├── llm.py              # THE ONLY place a model provider is named
│   ├── database.py         # read-only engine + sqlglot guard
│   ├── tools.py            # toolkit tools, guarded query, plot_result, capture
│   ├── prompts.py          # SQL rules + EAD domain glossary
│   ├── charts.py           # should_chart / validate_spec hard gates
│   └── graph.py            # create_agent + memory + ask() contract
├── api/main.py             # FastAPI (127.0.0.1)
├── ui/app.py               # Gradio, local testing only
├── eval/                   # gold.json + run_eval.py
└── tests/
```
