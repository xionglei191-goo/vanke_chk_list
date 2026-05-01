#!/usr/bin/env python3
"""Remove legacy audit state that is unrelated to the v3 AI-first direction."""
import argparse
import os
import shutil
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
APP_DIR = SCRIPT_DIR.parent
PROJECT_DIR = APP_DIR.parent
DATA_DIR = APP_DIR / "data"
ANALYSIS_DIR = DATA_DIR / "analysis"

PRESERVE_ANALYSIS = {
    "deep_alignment_benchmark_report.md",
    "unresolved_review_sources.md",
    "repair_review_playbook.md",
}


def _remove_path(path, apply):
    if not path.exists() and not path.is_symlink():
        return []
    if apply:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    return [str(path)]


def cleanup(apply=False):
    removed = []
    for path in [
        DATA_DIR / "results",
        APP_DIR / "temp_uploads",
        PROJECT_DIR / "temp_uploads",
        APP_DIR / "vector_db_storage",
        PROJECT_DIR / "vector_db_storage",
        PROJECT_DIR / "vector_db",
    ]:
        removed.extend(_remove_path(path, apply))

    for pattern in [
        "audit_queue.db",
        "llm_cache.db",
        "knowledge_base.db",
        "vanke_chk.db",
        "knowledge_base.json",
        "knowledge_base.json.*",
        "correction_cases.json",
        "standard_pdf_registry.json",
        ".kb_lock",
    ]:
        for path in DATA_DIR.glob(pattern):
            removed.extend(_remove_path(path, apply))

    if ANALYSIS_DIR.exists():
        for path in ANALYSIS_DIR.iterdir():
            if path.name in PRESERVE_ANALYSIS or path.name.startswith("v3_smoke_"):
                continue
            removed.extend(_remove_path(path, apply))
    return removed


def main():
    parser = argparse.ArgumentParser(description="Clean legacy audit state.")
    parser.add_argument("--apply", action="store_true", help="Actually delete files. Without this flag only prints the plan.")
    args = parser.parse_args()
    removed = cleanup(apply=args.apply)
    action = "removed" if args.apply else "would remove"
    print(f"{action}: {len(removed)} paths")
    for path in removed[:200]:
        print(path)
    if len(removed) > 200:
        print(f"... {len(removed) - 200} more")


if __name__ == "__main__":
    main()
