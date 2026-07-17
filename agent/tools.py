"""Agent tools.

From SQLDatabaseToolkit: sql_db_list_tables, sql_db_schema.
sql_db_query is replaced with a guarded version that (a) runs the sqlglot
guard before execution and (b) captures the structured result set, so the
client table is built from the SAME execution the model saw — the SQL is
never re-executed.

sql_db_query_checker is EXCLUDED by default (USE_QUERY_CHECKER=false): it is
an extra LLM round trip that in practice returns the query unchanged. The
database's own error feedback does the correction, grounded in a real
failure rather than a guess. Flip the env var to measure it on the eval set.
"""
import contextvars
from dataclasses import dataclass, field

from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_core.tools import tool

import config
from agent.database import SQLGuardError, get_db, run_query

# --- Per-run capture of what the tools actually did/saw -------------------


@dataclass
class RunCapture:
    sql: str | None = None
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    chart_spec: dict | None = None


_capture: contextvars.ContextVar = contextvars.ContextVar("run_capture", default=None)
_capture_fallback: RunCapture | None = None  # tool may run on a worker thread


def start_capture() -> RunCapture:
    global _capture_fallback
    cap = RunCapture()
    _capture.set(cap)
    _capture_fallback = cap
    return cap


def current_capture() -> RunCapture | None:
    return _capture.get() or _capture_fallback


# --- Tools ----------------------------------------------------------------


def _format_result(columns, rows) -> str:
    if not rows:
        return "Query returned no rows."
    lines = ["\t".join(columns)]
    for r in rows[:config.ROW_LIMIT]:
        lines.append("\t".join("NULL" if v is None else str(v) for v in r))
    if len(rows) > config.ROW_LIMIT:
        lines.append(f"... ({len(rows) - config.ROW_LIMIT} more rows truncated)")
    return "\n".join(lines)


@tool("sql_db_query")
def guarded_sql_db_query(query: str) -> str:
    """Execute a single SQL SELECT query against the database and return the
    result. If an error is returned, rewrite the query, check it, and try
    again. Only SELECT statements are permitted."""
    try:
        executed_sql, columns, rows = run_query(query)
    except SQLGuardError as e:
        return f"Error: query rejected by the safety guard: {e}"
    except Exception as e:
        return f"Error: {e}"
    cap = current_capture()
    if cap is not None:
        cap.sql = executed_sql
        cap.columns = columns
        cap.rows = rows
        cap.chart_spec = None   # a new result invalidates any earlier chart ask
    return _format_result(columns, rows)


@tool
def plot_result(chart_type: str, x_column: str, y_column: str, title: str) -> str:
    """Visualise the most recent query result.
    chart_type: 'bar' | 'line' | 'pie' | 'grouped_bar'
    x_column / y_column must be column names from that result (for
    grouped_bar, y_column may be two comma-separated columns).
    Call ONLY when a chart genuinely aids understanding. Do not chart single
    values or bare lists of names."""
    # Records a chart *specification* into agent state — no image is rendered
    # here. The client renders; the spec is validated against the actual
    # result set and dropped silently if inappropriate.
    cap = current_capture()
    if cap is not None:
        cap.chart_spec = {
            "type": chart_type, "x": x_column, "y": y_column, "title": title,
        }
    return "Chart specification recorded."


def get_tools(model):
    toolkit = SQLDatabaseToolkit(db=get_db(), llm=model)
    by_name = {t.name: t for t in toolkit.get_tools()}
    tools = [
        by_name["sql_db_list_tables"],
        by_name["sql_db_schema"],
        guarded_sql_db_query,
        plot_result,
    ]
    if config.USE_QUERY_CHECKER:
        tools.append(by_name["sql_db_query_checker"])
    return tools
