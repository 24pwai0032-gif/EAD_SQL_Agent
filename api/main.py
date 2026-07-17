"""FastAPI service. Bound to 127.0.0.1 — never exposed directly to a
browser; the Laravel/gateway layer owns auth in production.

POST /ask  {question, thread_id} -> {answer, sql, columns, rows, chart, trace}
GET  /health -> provider, model, tool-calling status
"""
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import config
from agent import llm

_state = {"tool_calling": False, "startup_error": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail loudly at startup: without tool calling nothing works.
    try:
        llm.verify_tool_calling()
        _state["tool_calling"] = True
    except Exception as e:
        _state["startup_error"] = str(e)
        print("\n" + "=" * 70)
        print("STARTUP FAILURE: tool-calling verification failed.")
        print(str(e))
        print("The /ask endpoint is disabled until this is fixed.")
        print("=" * 70 + "\n")
    yield


app = FastAPI(title="EAD SQL Agent", lifespan=lifespan)


class AskRequest(BaseModel):
    question: str
    thread_id: str = "default"


@app.get("/health")
def health():
    return {
        "status": "ok" if _state["tool_calling"] else "degraded",
        "provider": llm.PROVIDER,
        "model": llm.model_name(),
        "tool_calling": _state["tool_calling"],
        "error": _state["startup_error"],
    }


@app.post("/ask")
def ask_endpoint(req: AskRequest):
    if not _state["tool_calling"]:
        raise HTTPException(
            status_code=503,
            detail=f"Model tool-calling unavailable: {_state['startup_error']}")
    from agent.graph import ask
    try:
        return ask(req.question, thread_id=req.thread_id)
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Agent error; see server log.")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.API_HOST, port=config.API_PORT)
