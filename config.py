"""Central configuration. Reads environment only — no provider is named here.

The model provider is selected in exactly one place: agent/llm.py.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

# --- LLM (values only; interpretation lives in agent/llm.py) ---
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "").strip().lower()
LOCAL_BASE_URL = os.getenv("LOCAL_BASE_URL", "http://localhost:8000/v1")
LOCAL_MODEL = os.getenv("LOCAL_MODEL", "")  # default lives in agent/llm.py
LOCAL_API_KEY = os.getenv("LOCAL_API_KEY", "not-needed")

# --- Database ---
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite:///file:{PROJECT_ROOT / 'data' / 'ead.db'}?mode=ro&uri=true",
)

# The agent sees ONLY these views, never base tables.
AGENT_VIEWS = [
    "v_fea_transactions",
    "v_commitments",
    "v_disbursements",
    "v_debt_service",
]

# --- Safety limits ---
ROW_LIMIT = 50            # LIMIT injected into queries that lack one
MAX_ROWS_HARD_CAP = 200   # absolute cap on rows returned to the model
STATEMENT_TIMEOUT_S = 15  # per-query execution budget

# sql_db_query_checker is an extra LLM round trip that usually returns the
# query unchanged; the database's own error feedback does the correction.
# Off by default; flip on to measure against the eval set.
USE_QUERY_CHECKER = os.getenv("USE_QUERY_CHECKER", "false").strip().lower() == "true"

# --- API ---
API_HOST = "127.0.0.1"    # never expose directly; a gateway owns auth in production
API_PORT = int(os.getenv("API_PORT", "8080"))
