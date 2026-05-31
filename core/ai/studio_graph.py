"""Entry point for `langgraph dev` (LangGraph Studio).

Studio loads this module, grabs the `graph` symbol, and renders the compiled
state machine in its browser UI. The skeleton built by `build_graph()` is
deliberately simple (load_context → classify_intent → ... → final_response)
because the heavy lifting (orchestrator + ReAct subagent) is done outside the
graph. For deeper visibility into tool calls and the ReAct loop, enable
LangSmith tracing alongside Studio (env vars: LANGSMITH_TRACING=true,
LANGSMITH_API_KEY=..., LANGSMITH_PROJECT=bloomhub-ai).

Studio runs this file outside of `manage.py`, so Django must be bootstrapped
explicitly before any model import.
"""

from __future__ import annotations

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from core.ai.graph import build_graph  # noqa: E402 — must run after django.setup()

graph = build_graph()

if graph is None:
    raise RuntimeError(
        "LangGraph could not be built. Install `langgraph` and "
        "`langchain-openai`, set OPENROUTER_API_KEY, then retry."
    )
