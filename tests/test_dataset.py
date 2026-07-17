import sqlite3

from tests.conftest import DB_PATH


def q(sql):
    conn = sqlite3.connect(DB_PATH)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_row_and_column_counts():
    assert q("SELECT COUNT(*) FROM fea_transactions")[0][0] == 5000
    assert len(q("PRAGMA table_info(fea_transactions)")) == 20


def test_lookup_tables():
    assert q("SELECT COUNT(*) FROM wings")[0][0] == 9
    assert q("SELECT COUNT(*) FROM development_partners")[0][0] == 22
    assert q("SELECT COUNT(*) FROM provinces")[0][0] == 7
    assert q("SELECT COUNT(*) FROM sectors")[0][0] == 12


def test_grants_and_ta_have_null_interest_and_no_repayments():
    assert q("""SELECT COUNT(*) FROM fea_transactions
                WHERE assistance_type != 'Loan' AND interest_rate_pct IS NOT NULL""")[0][0] == 0
    assert q("""SELECT COUNT(*) FROM fea_transactions
                WHERE assistance_type != 'Loan'
                AND flow_type IN ('Principal Repayment','Interest Payment')""")[0][0] == 0


def test_loans_always_have_interest_rate():
    assert q("""SELECT COUNT(*) FROM fea_transactions
                WHERE assistance_type = 'Loan' AND interest_rate_pct IS NULL""")[0][0] == 0


def test_remarks_only_for_pending_rejected():
    assert q("""SELECT COUNT(*) FROM fea_transactions
                WHERE (status = 'Cleared') != (remarks IS NULL)""")[0][0] == 0


def test_amounts_positive_and_pkr_consistent():
    assert q("SELECT COUNT(*) FROM fea_transactions WHERE amount_usd_mn < 0")[0][0] == 0
    assert q("""SELECT COUNT(*) FROM fea_transactions
                WHERE ABS(amount_pkr_mn - amount_usd_mn * exchange_rate_pkr) > 0.5""")[0][0] == 0


def test_rupee_depreciates():
    rates = q("""SELECT fiscal_year, AVG(exchange_rate_pkr) FROM fea_transactions
                 GROUP BY fiscal_year ORDER BY fiscal_year""")
    by_fy = dict(rates)
    assert by_fy["FY2016"] < 115
    assert by_fy["FY2026"] > 270


def test_un_agencies_do_not_lend_billions():
    mx = q("""SELECT MAX(t.amount_usd_mn) FROM fea_transactions t
              JOIN development_partners p USING (partner_id)
              WHERE p.partner_type = 'UN agency'""")[0][0]
    assert mx < 50


def test_views_exist_and_are_joined():
    views = {r[0] for r in q("SELECT name FROM sqlite_master WHERE type='view'")}
    assert views == {"v_fea_transactions", "v_commitments", "v_disbursements", "v_debt_service"}
    row = q("SELECT partner_abbreviation, wing_name, sector_name FROM v_fea_transactions LIMIT 1")
    assert row and all(row[0])
