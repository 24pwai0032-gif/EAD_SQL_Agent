"""Live agent tests — require a configured model (see .env.example).
Skipped automatically when no credentials are present, so the deterministic
test suite stays runnable anywhere."""
import pytest

from agent.llm import has_credentials

pytestmark = pytest.mark.skipif(
    not has_credentials(), reason="no model credentials configured")


def test_tool_calling_works():
    from agent.llm import verify_tool_calling
    assert verify_tool_calling()


def test_ask_returns_full_contract():
    from agent.graph import ask
    result = ask("How many development partners are there?", thread_id="t-contract")
    assert set(result) == {"answer", "sql", "columns", "rows", "chart", "trace"}
    assert result["sql"] and "select" in result["sql"].lower()
    assert result["rows"]
    assert result["chart"] is None          # single value must never chart
    assert result["answer"]


def test_conversation_memory_resolves_followup():
    from agent.graph import ask
    tid = "t-memory"
    first = ask("Total cleared disbursements in FY2024, in USD?", thread_id=tid)
    followup = ask("And what about FY2023?", thread_id=tid)
    # The follow-up only makes sense if thread history carried through.
    assert followup["sql"] is not None
    assert "FY2023" in followup["sql"] or "FY2023" in followup["answer"]
    assert first["sql"] != followup["sql"]
