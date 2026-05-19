#!/usr/bin/env python3
"""Build the v3 repair review playbook from the deep alignment report."""
import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
APP_DIR = SCRIPT_DIR.parent
PROJECT_DIR = APP_DIR.parent
for path in (str(APP_DIR), str(PROJECT_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from rag_engine.playbook_manager import DEFAULT_OUTPUT, DEFAULT_SOURCE, build_playbook, rebuild_playbook


def main():
    parser = argparse.ArgumentParser(description="Build v3 repair review playbook.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    approved_feedback = ""
    try:
        from rag_engine.review_workflow import approved_lessons_markdown
        approved_feedback = approved_lessons_markdown()
    except Exception:
        approved_feedback = ""
    result = rebuild_playbook(args.source, args.output, approved_feedback=approved_feedback)
    if not result.get("ok"):
        raise SystemExit(result.get("error") or "failed to build playbook")
    print(f"playbook written: {result['output_path']}")
    print(f"chars: {result['chars']}")


if __name__ == "__main__":
    main()
