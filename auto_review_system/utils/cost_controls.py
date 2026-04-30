"""
Cost-control defaults for LLM-paid workflows.

The balanced profile keeps the expensive model for final audit conclusions and
uses local routing/filtering for cheap decisions.
"""
import os


def audit_cost_profile():
    profile = os.getenv("AUDIT_COST_PROFILE", "balanced").strip().lower()
    return profile if profile in {"balanced", "strict", "quality"} else "balanced"


def rag_rerank_mode():
    raw = os.getenv("RAG_RERANK_MODE", "").strip().lower()
    if raw in {"local", "llm", "off"}:
        return raw
    return "llm" if audit_cost_profile() == "quality" else "local"


def triage_mode():
    raw = os.getenv("TRIAGE_MODE", "").strip().lower()
    if raw in {"local", "llm", "off"}:
        return raw
    return "llm" if audit_cost_profile() == "quality" else "local"


def agent_routing_enabled():
    raw = os.getenv("AGENT_ROUTING_ENABLED", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return audit_cost_profile() != "quality"


def max_scheme_agents():
    raw = os.getenv("AGENT_MAX_SCHEME_AGENTS", "").strip()
    if raw.isdigit():
        return max(2, min(8, int(raw)))
    profile = audit_cost_profile()
    if profile == "strict":
        return 3
    if profile == "quality":
        return 8
    return 5
