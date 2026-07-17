"""Database access and safety.

Two independent layers stop writes:
  1. The connection itself: SQLite is opened mode=ro (in production this
     becomes a SELECT-only Postgres user). A write fails at the engine.
  2. The sqlglot guard: every generated query is parsed BEFORE execution.
     This is a real structural check, not a prompt instruction.

The agent sees ONLY the v_* views, never base tables (include_tables).
"""
import time

import sqlglot
from sqlglot import exp
from langchain_community.utilities import SQLDatabase
from sqlalchemy.dialects.sqlite.base import SQLiteDialect

import config

# SQLDatabase(view_support=True) queries materialized views, which the SQLite
# dialect does not implement (raises NotImplementedError). SQLite has no
# materialized views, so an empty list is the correct answer.
SQLiteDialect.get_materialized_view_names = lambda self, connection, schema=None, **kw: []


class SQLGuardError(ValueError):
    """Raised when a generated query is rejected before execution."""


_db = None


def get_db() -> SQLDatabase:
    global _db
    if _db is None:
        _db = SQLDatabase.from_uri(
            config.DATABASE_URL,
            include_tables=config.AGENT_VIEWS,
            sample_rows_in_table_info=0,
            view_support=True,
        )
    return _db


# Statement kinds that must never execute, wherever they appear in the tree.
_FORBIDDEN = tuple(
    getattr(exp, name)
    for name in (
        "Insert", "Update", "Delete", "Drop", "Alter", "Create", "Merge",
        "TruncateTable", "Attach", "Detach", "Pragma", "Command", "Transaction",
        "Grant", "Set",
    )
    if hasattr(exp, name)
)

_SELECT_ROOTS = tuple(
    getattr(exp, name) for name in ("Select", "Union", "Except", "Intersect")
    if hasattr(exp, name)
)


def guard_sql(sql: str) -> str:
    """Parse `sql`; reject anything that is not a single SELECT.

    Returns the query to execute, with a LIMIT injected if absent.
    Raises SQLGuardError otherwise.
    """
    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise SQLGuardError(f"Query could not be parsed: {e}") from e

    if len(statements) != 1:
        raise SQLGuardError("Exactly one statement is allowed per query.")

    root = statements[0]
    if not isinstance(root, _SELECT_ROOTS):
        raise SQLGuardError(
            f"Only SELECT queries are allowed; got {root.key.upper()}.")

    for node in root.walk():
        if isinstance(node, _FORBIDDEN):
            raise SQLGuardError(
                f"Forbidden operation in query: {node.key.upper()}.")

    if root.args.get("limit") is None:
        root = root.limit(config.ROW_LIMIT)
    return root.sql(dialect="sqlite")


def run_query(sql: str):
    """Guard, then execute. Returns (executed_sql, columns, rows).

    Enforces a statement timeout via SQLite's progress handler and a hard
    row cap on top of the injected LIMIT.
    """
    safe_sql = guard_sql(sql)
    engine = get_db()._engine
    with engine.connect() as conn:
        raw = conn.connection.driver_connection
        deadline = time.monotonic() + config.STATEMENT_TIMEOUT_S
        raw.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        try:
            from sqlalchemy import text
            result = conn.execute(text(safe_sql))
            columns = list(result.keys())
            rows = [list(r) for r in result.fetchmany(config.MAX_ROWS_HARD_CAP)]
        finally:
            raw.set_progress_handler(None, 0)
    return safe_sql, columns, rows
