import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

import config
from agent.database import SQLGuardError, get_db, guard_sql, run_query


# --- Layer 1: the sqlglot guard ------------------------------------------

@pytest.mark.parametrize("sql", [
    "DROP TABLE fea_transactions",
    "DELETE FROM fea_transactions",
    "INSERT INTO wings (name) VALUES ('x')",
    "UPDATE fea_transactions SET amount_usd_mn = 0",
    "ALTER TABLE wings ADD COLUMN x TEXT",
    "CREATE TABLE t (x INT)",
    "ATTACH DATABASE '/tmp/evil.db' AS evil",
    "PRAGMA writable_schema = 1",
])
def test_guard_rejects_writes(sql):
    with pytest.raises(SQLGuardError):
        guard_sql(sql)


def test_guard_rejects_multiple_statements():
    with pytest.raises(SQLGuardError):
        guard_sql("SELECT 1; DROP TABLE fea_transactions")


def test_guard_rejects_sneaky_nested_write():
    with pytest.raises(SQLGuardError):
        guard_sql("SELECT * FROM v_commitments; DELETE FROM wings")


def test_guard_injects_limit():
    out = guard_sql("SELECT fiscal_year FROM v_commitments")
    assert f"LIMIT {config.ROW_LIMIT}" in out


def test_guard_keeps_existing_limit():
    out = guard_sql("SELECT fiscal_year FROM v_commitments LIMIT 3")
    assert "LIMIT 3" in out


def test_guard_allows_cte_and_union():
    guard_sql("WITH t AS (SELECT fiscal_year FROM v_commitments) SELECT * FROM t")
    guard_sql("SELECT fiscal_year FROM v_commitments UNION SELECT fiscal_year FROM v_disbursements")


# --- Layer 2: the connection itself is read-only --------------------------

def test_connection_is_read_only():
    engine = get_db()._engine
    with pytest.raises((OperationalError, DBAPIError)):
        with engine.connect() as conn:
            conn.execute(text("DROP TABLE fea_transactions"))
            conn.commit()


def test_run_query_end_to_end():
    sql, cols, rows = run_query(
        "SELECT fiscal_year, SUM(amount_usd_mn) AS total FROM v_commitments GROUP BY fiscal_year")
    assert cols == ["fiscal_year", "total"]
    assert 1 < len(rows) <= config.MAX_ROWS_HARD_CAP
    assert "LIMIT" in sql


def test_run_query_rejects_drop():
    with pytest.raises(SQLGuardError):
        run_query("DROP TABLE fea_transactions")


def test_agent_sees_only_views():
    names = set(get_db().get_usable_table_names())
    assert names == set(config.AGENT_VIEWS)
