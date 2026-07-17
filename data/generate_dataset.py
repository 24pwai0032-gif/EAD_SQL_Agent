"""Generate data/ead.db from scratch.

Idempotent: the database file is deleted and recreated on every run.
random.seed(42) keeps the numbers reproducible so the eval set stays stable.

Models Foreign Economic Assistance flows (EAD mandate) — NOT domestic PSDP
budget spending: commitments, disbursements and external debt service from
foreign governments and multilateral agencies.
"""
import os
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "ead.db"
N_ROWS = 5000

random.seed(42)

# ---------------------------------------------------------------------------
# Lookup data
# ---------------------------------------------------------------------------

WINGS = [
    "World Bank Wing",
    "ADB/Japan Wing",
    "Administration Wing",
    "UN Wing",
    "China Wing",
    "Paris Club Wing",
    "Economic Cooperation Wing",
    "Devolution-I Wing",
    "Research & Statistics/IT/Debt Management Wing",
]
WING_ID = {name: i + 1 for i, name in enumerate(WINGS)}

# (name, abbreviation, partner_type, country, wing, is_paris_club, scale)
# scale drives commitment sizes: UN agencies do not lend billions.
PARTNERS = [
    ("International Bank for Reconstruction and Development", "IBRD", "multilateral", None, "World Bank Wing", 0, "large"),
    ("International Development Association", "IDA", "multilateral", None, "World Bank Wing", 0, "large"),
    ("International Finance Corporation", "IFC", "multilateral", None, "World Bank Wing", 0, "medium"),
    ("Asian Development Bank", "ADB", "multilateral", None, "ADB/Japan Wing", 0, "large"),
    ("Japan International Cooperation Agency", "JICA", "bilateral", "Japan", "ADB/Japan Wing", 1, "large"),
    ("Export-Import Bank of China", "CEXIM", "bilateral", "China", "China Wing", 0, "large"),
    ("China International Development Cooperation Agency", "CIDCA", "bilateral", "China", "China Wing", 0, "medium"),
    ("United Nations Development Programme", "UNDP", "UN agency", None, "UN Wing", 0, "small"),
    ("UN Economic and Social Commission for Asia and the Pacific", "ESCAP", "UN agency", None, "UN Wing", 0, "small"),
    ("Colombo Plan", "Colombo Plan", "multilateral", None, "UN Wing", 0, "small"),
    ("KfW Development Bank", "KfW", "bilateral", "Germany", "Paris Club Wing", 1, "medium"),
    ("Agence Française de Développement", "AFD", "bilateral", "France", "Paris Club Wing", 1, "medium"),
    ("United States Agency for International Development", "USAID", "bilateral", "United States", "Paris Club Wing", 1, "medium"),
    ("Foreign, Commonwealth & Development Office", "FCDO", "bilateral", "United Kingdom", "Paris Club Wing", 1, "medium"),
    ("Islamic Development Bank", "IsDB", "multilateral", None, "Economic Cooperation Wing", 0, "large"),
    ("Saudi Fund for Development", "SFD", "bilateral", "Saudi Arabia", "Economic Cooperation Wing", 0, "medium"),
    ("Kuwait Fund for Arab Economic Development", "KFAED", "bilateral", "Kuwait", "Economic Cooperation Wing", 0, "medium"),
    ("OPEC Fund for International Development", "OFID", "multilateral", None, "Economic Cooperation Wing", 0, "medium"),
    ("Asian Infrastructure Investment Bank", "AIIB", "multilateral", None, "Economic Cooperation Wing", 0, "large"),
    ("Economic Development Cooperation Fund (Korea)", "EDCF", "bilateral", "South Korea", "Economic Cooperation Wing", 1, "medium"),
    ("European Union", "EU", "multilateral", None, "Economic Cooperation Wing", 0, "medium"),
    ("International Fund for Agricultural Development", "IFAD", "multilateral", None, "Economic Cooperation Wing", 0, "medium"),
]

SECTORS = [
    "Energy", "Transport", "Water & Sanitation", "Health", "Education",
    "Agriculture", "Governance", "Climate", "Social Protection", "Finance",
    "Disaster Rehabilitation", "Trade",
]

PROVINCES = ["Punjab", "Sindh", "Khyber Pakhtunkhwa", "Balochistan",
             "Gilgit-Baltistan", "Azad Jammu & Kashmir", "Islamabad Capital Territory"]
PROVINCE_ID = {name: i + 1 for i, name in enumerate(PROVINCES)}

# (name, province or None for federal)
AGENCIES = [
    ("Power Division, Ministry of Energy", None),
    ("National Highway Authority", None),
    ("Ministry of Water Resources", None),
    ("Water and Power Development Authority (WAPDA)", None),
    ("Ministry of National Health Services", None),
    ("Ministry of Federal Education & Professional Training", None),
    ("Ministry of National Food Security & Research", None),
    ("Finance Division", None),
    ("National Disaster Management Authority", None),
    ("Ministry of Climate Change & Environmental Coordination", None),
    ("Ministry of Commerce", None),
    ("Ministry of IT & Telecommunication", None),
    ("National Transmission & Despatch Company", None),
    ("Pakistan Poverty Alleviation Fund", None),
    ("Planning & Development Board, Punjab", "Punjab"),
    ("Planning & Development Department, Sindh", "Sindh"),
    ("Planning & Development Department, Khyber Pakhtunkhwa", "Khyber Pakhtunkhwa"),
    ("Planning & Development Department, Balochistan", "Balochistan"),
    ("Planning & Development Department, Gilgit-Baltistan", "Gilgit-Baltistan"),
    ("Planning & Development Department, Azad Jammu & Kashmir", "Azad Jammu & Kashmir"),
]

FISCAL_YEARS = [f"FY{y}" for y in range(2016, 2027)]  # FY2016..FY2026

# PKR/USD mid-rate by fiscal year: the rupee depreciates sharply over the period.
FX_BASE = {
    "FY2016": 105, "FY2017": 105, "FY2018": 112, "FY2019": 136, "FY2020": 158,
    "FY2021": 160, "FY2022": 178, "FY2023": 248, "FY2024": 283, "FY2025": 281,
    "FY2026": 285,
}

COMMITMENT_RANGE = {"large": (100, 900), "medium": (8, 120), "small": (0.5, 12)}

PENDING_REMARKS = [
    "Awaiting NOC from Finance Division",
    "Withdrawal application under review by partner",
    "Legal opinion pending from Law & Justice Division",
    "Audited financial statements awaited from executing agency",
    "Subsidiary loan agreement not yet signed",
]
REJECTED_REMARKS = [
    "Withdrawal application returned — documentation incomplete",
    "Expenditure ineligible under financing agreement",
    "Claim outside project closing date",
    "Category reallocation required before disbursement",
]
DEBT_SERVICE_PENDING_REMARKS = [
    "Remittance authorisation awaiting State Bank confirmation",
    "Debt service payment advice under reconciliation with creditor",
]
DEBT_SERVICE_REJECTED_REMARKS = [
    "Payment instruction returned — creditor account details mismatch",
    "Billing statement disputed with creditor",
]


def fy_of(d: date) -> str:
    """Pakistan fiscal year: 1 July – 30 June. July 2023 falls in FY2024."""
    return f"FY{d.year + 1}" if d.month >= 7 else f"FY{d.year}"


def fy_quarter(d: date) -> int:
    """Fiscal quarter: Q1=Jul-Sep, Q2=Oct-Dec, Q3=Jan-Mar, Q4=Apr-Jun."""
    return {7: 1, 8: 1, 9: 1, 10: 2, 11: 2, 12: 2,
            1: 3, 2: 3, 3: 3, 4: 4, 5: 4, 6: 4}[d.month]


def random_date_in_fy(fy: str) -> date:
    start_year = int(fy[2:]) - 1
    start = date(start_year, 7, 1)
    return start + timedelta(days=random.randint(0, 364))


def fx_rate(fy: str) -> float:
    return round(FX_BASE[fy] * random.uniform(0.985, 1.015), 2)


def pick_status(flow: str):
    debt_service = flow in ("Principal Repayment", "Interest Payment")
    r = random.random()
    if r < 0.92:
        return "Cleared", None
    if r < 0.97:
        return "Pending", random.choice(
            DEBT_SERVICE_PENDING_REMARKS if debt_service else PENDING_REMARKS)
    return "Rejected", random.choice(
        DEBT_SERVICE_REJECTED_REMARKS if debt_service else REJECTED_REMARKS)


def choose_assistance_type(partner_type: str, scale: str, abbrev: str) -> str:
    if partner_type == "UN agency" or abbrev == "Colombo Plan":
        return random.choices(["Grant", "Technical Assistance"], [0.55, 0.45])[0]
    if abbrev in ("USAID", "FCDO", "EU", "CIDCA"):
        return random.choices(["Grant", "Technical Assistance", "Loan"], [0.6, 0.25, 0.15])[0]
    # Development banks and lending bilaterals: mostly loans.
    return random.choices(["Loan", "Grant", "Technical Assistance"], [0.78, 0.14, 0.08])[0]


def choose_interest_rate(abbrev: str):
    concessional_lenders = {"IDA", "IsDB", "JICA", "EDCF", "SFD", "KFAED", "OFID", "IFAD"}
    if abbrev in concessional_lenders:
        return round(random.uniform(0.75, 2.0), 2)
    return round(random.uniform(2.0, 6.0), 2)


def main():
    if DB_PATH.exists():
        os.remove(DB_PATH)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # --- Schema -----------------------------------------------------------
    cur.executescript("""
    CREATE TABLE wings (
        wing_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE
    );
    CREATE TABLE development_partners (
        partner_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        abbreviation TEXT NOT NULL UNIQUE,
        partner_type TEXT NOT NULL CHECK (partner_type IN ('multilateral','bilateral','UN agency')),
        country TEXT,
        wing_id INTEGER NOT NULL REFERENCES wings(wing_id),
        is_paris_club INTEGER NOT NULL CHECK (is_paris_club IN (0,1))
    );
    CREATE TABLE sectors (
        sector_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE
    );
    CREATE TABLE provinces (
        province_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE
    );
    CREATE TABLE executing_agencies (
        agency_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        province_id INTEGER REFERENCES provinces(province_id)  -- NULL = federal
    );
    CREATE TABLE fea_transactions (
        transaction_id INTEGER PRIMARY KEY,
        transaction_ref TEXT NOT NULL UNIQUE,
        agreement_ref TEXT NOT NULL,
        partner_id INTEGER NOT NULL REFERENCES development_partners(partner_id),
        wing_id INTEGER NOT NULL REFERENCES wings(wing_id),
        agency_id INTEGER NOT NULL REFERENCES executing_agencies(agency_id),
        sector_id INTEGER NOT NULL REFERENCES sectors(sector_id),
        province_id INTEGER REFERENCES provinces(province_id),  -- NULL = federal project
        assistance_type TEXT NOT NULL CHECK (assistance_type IN ('Loan','Grant','Technical Assistance')),
        flow_type TEXT NOT NULL CHECK (flow_type IN ('Commitment','Disbursement','Principal Repayment','Interest Payment')),
        fiscal_year TEXT NOT NULL,
        quarter INTEGER NOT NULL CHECK (quarter BETWEEN 1 AND 4),
        transaction_date TEXT NOT NULL,
        amount_usd_mn REAL NOT NULL,
        exchange_rate_pkr REAL NOT NULL,
        amount_pkr_mn REAL NOT NULL,
        interest_rate_pct REAL,        -- NULL for grants and TA: "not a loan", not 0%
        is_concessional INTEGER NOT NULL CHECK (is_concessional IN (0,1)),
        status TEXT NOT NULL CHECK (status IN ('Cleared','Pending','Rejected')),
        remarks TEXT                   -- populated only for Pending/Rejected
    );
    """)

    cur.executemany("INSERT INTO wings (wing_id, name) VALUES (?,?)",
                    [(i + 1, w) for i, w in enumerate(WINGS)])
    cur.executemany(
        "INSERT INTO development_partners (partner_id, name, abbreviation, partner_type, country, wing_id, is_paris_club) VALUES (?,?,?,?,?,?,?)",
        [(i + 1, n, ab, pt, c, WING_ID[w], pc) for i, (n, ab, pt, c, w, pc, _s) in enumerate(PARTNERS)])
    cur.executemany("INSERT INTO sectors (sector_id, name) VALUES (?,?)",
                    [(i + 1, s) for i, s in enumerate(SECTORS)])
    cur.executemany("INSERT INTO provinces (province_id, name) VALUES (?,?)",
                    [(i + 1, p) for i, p in enumerate(PROVINCES)])
    cur.executemany("INSERT INTO executing_agencies (agency_id, name, province_id) VALUES (?,?,?)",
                    [(i + 1, n, PROVINCE_ID[p] if p else None) for i, (n, p) in enumerate(AGENCIES)])

    # --- Transactions -----------------------------------------------------
    # Each financing agreement produces a realistic lifecycle of rows:
    #   1. one Commitment (the signing),
    #   2. several Disbursement tranches spread over later fiscal years,
    #   3. for Loans only: yearly Principal Repayment + Interest Payment
    #      rows after a grace period (grants/TA are never repaid).
    # We keep generating agreements until we have at least N_ROWS
    # transactions, then trim to exactly N_ROWS.
    rows = []
    agr_seq = 0
    txn_seq = 0

    def add_row(agreement_ref, partner_idx, agency_idx, sector_id, atype, flow,
                d, usd, rate_pct):
        nonlocal txn_seq
        txn_seq += 1
        _n, abbrev, _pt, _c, wing, _pc, _scale = PARTNERS[partner_idx]
        fy = fy_of(d)
        fx = fx_rate(fy)
        status, remarks = pick_status(flow)
        rows.append((
            f"EAD/{fy}/{abbrev}/{txn_seq:06d}",
            agreement_ref,
            partner_idx + 1,
            WING_ID[wing],
            agency_idx + 1,
            sector_id,
            AGENCIES[agency_idx][1] and PROVINCE_ID[AGENCIES[agency_idx][1]],
            atype,
            flow,
            fy,
            fy_quarter(d),
            d.isoformat(),
            round(usd, 3),
            fx,
            round(usd * fx, 2),
            rate_pct,
            1 if (atype != "Loan" or (rate_pct is not None and rate_pct < 2.5)) else 0,
            status,
            remarks,
        ))

    while len(rows) < N_ROWS + 400:  # overshoot, then trim to exactly N_ROWS
        agr_seq += 1
        p_idx = random.randrange(len(PARTNERS))
        name, abbrev, ptype, _c, _w, _pc, scale = PARTNERS[p_idx]
        atype = choose_assistance_type(ptype, scale, abbrev)
        rate = choose_interest_rate(abbrev) if atype == "Loan" else None
        agency_idx = random.randrange(len(AGENCIES))
        sector_id = random.randrange(len(SECTORS)) + 1
        sign_fy_i = random.randrange(0, len(FISCAL_YEARS) - 1)  # FY2016..FY2025
        sign_fy = FISCAL_YEARS[sign_fy_i]
        sign_date = random_date_in_fy(sign_fy)
        agreement_ref = f"EAD/{sign_date.year}/{abbrev}/{agr_seq:04d}"

        lo, hi = COMMITMENT_RANGE[scale]
        commitment = random.uniform(lo, hi)
        if atype == "Technical Assistance":
            commitment = min(commitment, 15) * random.uniform(0.2, 1.0)
        elif atype == "Grant" and scale == "large":
            commitment *= random.uniform(0.1, 0.4)  # grants smaller than loans

        add_row(agreement_ref, p_idx, agency_idx, sector_id, atype,
                "Commitment", sign_date, commitment, rate)

        # Disbursement tranches over the years after signing.
        n_tranches = random.randint(2, 6)
        shares = [random.uniform(0.5, 1.5) for _ in range(n_tranches)]
        disb_total = commitment * random.uniform(0.55, 0.95)
        disbursed = 0.0
        for t, share in enumerate(shares):
            fy_i = min(sign_fy_i + (t + 1) // 2, len(FISCAL_YEARS) - 1)
            d = random_date_in_fy(FISCAL_YEARS[fy_i])
            amt = disb_total * share / sum(shares)
            disbursed += amt
            add_row(agreement_ref, p_idx, agency_idx, sector_id, atype,
                    "Disbursement", d, amt, rate)

        # Debt service: loans only. Grants and TA are never repaid.
        if atype == "Loan":
            grace = random.randint(2, 4)
            maturity = random.randint(15, 25)
            outstanding = disbursed
            for fy_i in range(sign_fy_i + grace, len(FISCAL_YEARS)):
                d = random_date_in_fy(FISCAL_YEARS[fy_i])
                principal = disbursed / maturity
                outstanding = max(outstanding - principal, 0.0)
                add_row(agreement_ref, p_idx, agency_idx, sector_id, atype,
                        "Principal Repayment", d, principal, rate)
                interest = max(outstanding, 0.0) * (rate / 100.0)
                if interest > 0.0005:
                    add_row(agreement_ref, p_idx, agency_idx, sector_id, atype,
                            "Interest Payment", d + timedelta(days=1), interest, rate)

    rows = rows[:N_ROWS]
    rows.sort(key=lambda r: r[11])  # by transaction_date
    cur.executemany(
        "INSERT INTO fea_transactions (transaction_ref, agreement_ref, partner_id, wing_id,"
        " agency_id, sector_id, province_id, assistance_type, flow_type, fiscal_year, quarter,"
        " transaction_date, amount_usd_mn, exchange_rate_pkr, amount_pkr_mn, interest_rate_pct,"
        " is_concessional, status, remarks) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows)

    # --- Views: the agent sees ONLY these, never base tables --------------
    cur.executescript("""
    CREATE VIEW v_fea_transactions AS
    SELECT
        t.transaction_id,
        t.transaction_ref,
        t.agreement_ref,
        p.name             AS partner_name,
        p.abbreviation     AS partner_abbreviation,
        p.partner_type,
        p.country          AS partner_country,
        p.is_paris_club,
        w.name             AS wing_name,
        a.name             AS agency_name,
        s.name             AS sector_name,
        pr.name            AS province_name,   -- NULL = federal project
        t.assistance_type,
        t.flow_type,
        t.fiscal_year,
        t.quarter,
        t.transaction_date,
        t.amount_usd_mn,
        t.exchange_rate_pkr,
        t.amount_pkr_mn,
        t.interest_rate_pct,                    -- NULL = not a loan
        t.is_concessional,
        t.status,
        t.remarks
    FROM fea_transactions t
    JOIN development_partners p ON p.partner_id = t.partner_id
    JOIN wings w                ON w.wing_id = t.wing_id
    JOIN executing_agencies a   ON a.agency_id = t.agency_id
    JOIN sectors s              ON s.sector_id = t.sector_id
    LEFT JOIN provinces pr      ON pr.province_id = t.province_id;

    CREATE VIEW v_commitments AS
    SELECT * FROM v_fea_transactions WHERE flow_type = 'Commitment';

    CREATE VIEW v_disbursements AS
    SELECT * FROM v_fea_transactions WHERE flow_type = 'Disbursement';

    CREATE VIEW v_debt_service AS
    SELECT * FROM v_fea_transactions
    WHERE flow_type IN ('Principal Repayment', 'Interest Payment');
    """)
    conn.commit()

    # --- Verification -----------------------------------------------------
    print("=== Row counts ===")
    for table in ["wings", "development_partners", "sectors", "provinces",
                  "executing_agencies", "fea_transactions"]:
        n = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:24s} {n:6d}")

    ncols = len(cur.execute("PRAGMA table_info(fea_transactions)").fetchall())
    print(f"\nfea_transactions columns: {ncols}")

    print("\n=== Sanity checks ===")
    checks = {
        "rows == 5000":
            cur.execute("SELECT COUNT(*) FROM fea_transactions").fetchone()[0] == N_ROWS,
        "columns == 20": ncols == 20,
        "grants/TA with an interest rate (must be 0)":
            cur.execute("SELECT COUNT(*) FROM fea_transactions WHERE assistance_type != 'Loan' AND interest_rate_pct IS NOT NULL").fetchone()[0] == 0,
        "repayment/interest rows on non-loans (must be 0)":
            cur.execute("SELECT COUNT(*) FROM fea_transactions WHERE assistance_type != 'Loan' AND flow_type IN ('Principal Repayment','Interest Payment')").fetchone()[0] == 0,
        "loans without an interest rate (must be 0)":
            cur.execute("SELECT COUNT(*) FROM fea_transactions WHERE assistance_type = 'Loan' AND interest_rate_pct IS NULL").fetchone()[0] == 0,
        "remarks present iff Pending/Rejected":
            cur.execute("SELECT COUNT(*) FROM fea_transactions WHERE (status='Cleared') != (remarks IS NULL)").fetchone()[0] == 0,
        "all amounts stored positive":
            cur.execute("SELECT COUNT(*) FROM fea_transactions WHERE amount_usd_mn < 0").fetchone()[0] == 0,
        "amount_pkr_mn = usd * rate":
            cur.execute("SELECT COUNT(*) FROM fea_transactions WHERE ABS(amount_pkr_mn - amount_usd_mn * exchange_rate_pkr) > 0.5").fetchone()[0] == 0,
    }
    failed = False
    for label, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        failed = failed or not ok

    pct = cur.execute(
        "SELECT ROUND(100.0*SUM(status!='Cleared')/COUNT(*),1) FROM fea_transactions").fetchone()[0]
    print(f"\nPending/Rejected share: {pct}% (target ~8%)")

    print("\n=== Rupee depreciation (avg exchange_rate_pkr by FY) ===")
    for fy, r in cur.execute(
            "SELECT fiscal_year, ROUND(AVG(exchange_rate_pkr),1) FROM fea_transactions GROUP BY fiscal_year ORDER BY fiscal_year"):
        print(f"  {fy}: {r}")

    print("\n=== Scale by partner type (avg commitment, USD mn) ===")
    for pt, avg, mx in cur.execute("""
            SELECT p.partner_type, ROUND(AVG(t.amount_usd_mn),1), ROUND(MAX(t.amount_usd_mn),1)
            FROM fea_transactions t JOIN development_partners p USING (partner_id)
            WHERE t.flow_type='Commitment' GROUP BY p.partner_type"""):
        print(f"  {pt:14s} avg {avg:8.1f}   max {mx:8.1f}")

    conn.close()
    if failed:
        raise SystemExit("Sanity checks FAILED")
    print(f"\nOK: {DB_PATH} written.")


if __name__ == "__main__":
    main()
