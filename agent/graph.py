"""Agent assembly and the ask() entry point.

create_agent (prebuilt ReAct pattern) + InMemorySaver checkpointer so that
conversation memory works: follow-ups like "and what about FY2023?" resolve
via the thread_id.

The result table returned to clients is captured from the tool execution
itself (agent/tools.py RunCapture) — the SQL is NEVER re-executed to build
the dataframe. Re-running would double database load and let the prose and
the table disagree if data changed between runs.
"""
import threading

from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver

from agent import charts
from agent.llm import get_model
from agent.prompts import SYSTEM_PROMPT
from agent.tools import get_tools, start_capture

_agent = None
_agent_lock = threading.Lock()
_ask_lock = threading.Lock()


def build_agent():
    model = get_model()
    return create_agent(
        model,
        get_tools(model),
        system_prompt=SYSTEM_PROMPT,
        checkpointer=InMemorySaver(),
    )


def get_agent():
    global _agent
    with _agent_lock:
        if _agent is None:
            _agent = build_agent()
    return _agent


def _describe_tool_call(tool_call) -> str:
    args = ", ".join(f"{k}={str(v)[:120]}" for k, v in (tool_call.get("args") or {}).items())
    return f"{tool_call['name']}({args})"


def ask(question: str, thread_id: str = "default") -> dict:
    """Ask the agent a question. Returns the full response contract:

    {"answer", "sql", "columns", "rows", "chart", "trace"}
    """
    agent = get_agent()
    with _ask_lock:  # captures are per-run; serialize runs in this process
        cap = start_capture()
        trace: list[str] = []
        answer = ""
        for update in agent.stream(
            {"messages": [{"role": "user", "content": question}]},
            {"configurable": {"thread_id": thread_id}},
            stream_mode="updates",
        ):
            for node_output in update.values():
                for msg in (node_output or {}).get("messages", []):
                    if getattr(msg, "tool_calls", None):
                        trace.extend(_describe_tool_call(tc) for tc in msg.tool_calls)
                    msg_type = getattr(msg, "type", "")
                    if msg_type == "tool" and str(msg.content).startswith("Error:"):
                        trace.append(f"  -> {str(msg.content)[:200]}")
                    if msg_type == "ai" and not getattr(msg, "tool_calls", None):
                        answer = msg.text() if callable(getattr(msg, "text", None)) else str(msg.content)

        # The model proposes; code disposes: validate any chart spec against
        # the actual result set, dropping inappropriate ones silently.
        chart = charts.validate_spec(cap.chart_spec, cap.columns, cap.rows)

        return {
            "answer": answer,
            "sql": cap.sql,
            "columns": cap.columns,
            "rows": cap.rows,
            "chart": chart,
            "trace": trace,
        }
