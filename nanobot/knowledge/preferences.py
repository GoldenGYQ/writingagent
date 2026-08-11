"""Resolve user-configured defaults for Knowledge retrieval.

The resolver is deliberately small and side-effect free.  Settings are read
at tool execution time so a WebUI change applies to the next retrieval without
restarting the Agent Runtime.  Explicit tool arguments always win over these
defaults.
"""

from __future__ import annotations

from typing import Any

from nanobot.config.loader import load_config
from nanobot.config.schema import KnowledgeRetrievalConfig


def load_knowledge_retrieval_preferences() -> KnowledgeRetrievalConfig:
    """Load preferences, falling back to safe defaults for legacy configs."""

    try:
        preferences = load_config().agents.defaults.knowledge_retrieval
    except Exception:
        return KnowledgeRetrievalConfig()
    return preferences


def resolve_search_options(
    *,
    mode: str | None,
    limit: int | None,
    expand_hops: int | None,
) -> dict[str, Any]:
    """Resolve one ``knowledge_search`` call's effective options."""

    preferences = load_knowledge_retrieval_preferences()
    manual = preferences.parameter_mode == "manual"
    return {
        "mode": mode or (preferences.mode if manual else "hybrid"),
        "limit": max(1, min(20, limit if limit is not None else (preferences.top_k if manual else 8))),
        "expand_hops": max(
            0,
            min(2, expand_hops if expand_hops is not None else (preferences.expand_hops if manual else 1)),
        ),
        "preferences": preferences,
    }


def resolve_research_options(
    *,
    mode: str | None,
    budget: int | None,
    min_documents: int | None,
    expand_hops: int | None,
) -> dict[str, Any]:
    """Resolve one bounded ``knowledge_research`` orchestration call."""

    preferences = load_knowledge_retrieval_preferences()
    manual = preferences.parameter_mode == "manual"
    return {
        "mode": mode or (preferences.mode if manual else "hybrid"),
        "budget": max(1, min(4, budget if budget is not None else (preferences.max_queries if manual else 3))),
        "min_documents": max(
            1,
            min(8, min_documents if min_documents is not None else (preferences.min_documents if manual else 3)),
        ),
        "expand_hops": max(
            0,
            min(2, expand_hops if expand_hops is not None else (preferences.expand_hops if manual else 1)),
        ),
        "preferences": preferences,
    }


def allow_query_rewrite(
    queries: list[str] | None,
    *,
    preferences: KnowledgeRetrievalConfig,
) -> list[str] | None:
    """Apply the configured query-rewrite policy to agent-supplied queries.

    Query rewriting is intentionally agent/tool driven: this function does
    not call an LLM.  ``off`` keeps only the original question; ``auto`` and
    ``manual`` allow the bounded query list supplied to ``knowledge_research``.
    """

    if preferences.query_rewrite == "off":
        return None
    return queries
