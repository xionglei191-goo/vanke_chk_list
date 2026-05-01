"""Compatibility wrapper for the v3 review feedback workflow."""
from rag_engine.review_workflow import (  # noqa: F401
    format_few_shot_prompt,
    get_correction_cases,
    record_correction,
)
