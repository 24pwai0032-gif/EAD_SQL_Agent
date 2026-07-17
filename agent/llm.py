"""Model layer — THE ONLY place a model provider is named.

Deployment reality: production runs fully on-premise (NTC) against a locally
hosted OpenAI-compatible endpoint (vLLM / Ollama / TGI). Development and
testing use the OpenAI API. Switching is LLM_PROVIDER=local in .env — no
other file changes, no other file may name a provider.
"""
import os

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

import config

PROVIDER = config.LLM_PROVIDER or "openai"


def has_credentials() -> bool:
    """Cheap, offline check that a model is configured at all.

    Used to skip live tests; keeps provider-specific env-var names out of
    every other file.
    """
    if PROVIDER == "openai":
        return bool(os.getenv("OPENAI_API_KEY"))
    return True  # local endpoints typically need no key


def get_model():
    """Return a chat model. Switch providers via LLM_PROVIDER env var only."""
    if PROVIDER == "openai":
        # Reads OPENAI_API_KEY from the environment; never pass keys around.
        return ChatOpenAI(model="gpt-4o-mini", temperature=0, timeout=90)
    return ChatOpenAI(                      # OpenAI-compatible: vLLM/Ollama/TGI
        model=config.LOCAL_MODEL or "qwen2.5-coder-32b-instruct",
        base_url=config.LOCAL_BASE_URL,
        api_key=config.LOCAL_API_KEY, temperature=0, timeout=180,
    )


def model_name() -> str:
    """For /health reporting only."""
    try:
        m = get_model()
    except Exception:
        return "unavailable (model not configured)"
    return getattr(m, "model_name", None) or getattr(m, "model", "unknown")


def verify_tool_calling(model=None) -> bool:
    """Bind a trivial tool and assert a tool call comes back.

    Without tool calling nothing works: the agent cannot inspect schemas or
    run SQL. Called at startup; raises loudly on failure so a misconfigured
    local model is caught immediately, not mid-demo.
    """

    @tool
    def ping(text: str) -> str:
        """Echo the given text back."""
        return text

    model = model or get_model()
    bound = model.bind_tools([ping])
    msg = bound.invoke("Call the ping tool with the text 'pong'. You must use the tool.")
    if not getattr(msg, "tool_calls", None):
        raise RuntimeError(
            f"Model (provider={PROVIDER!r}) did not return a tool call. "
            "The configured model does not support tool calling — the agent cannot work. "
            "Check the model/endpoint configuration in .env."
        )
    return True
