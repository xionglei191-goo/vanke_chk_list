"""
Runtime path helpers.

All application data paths are anchored to auto_review_system/ so the app keeps
working no matter where Streamlit, scripts, or the worker are launched from.
"""
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_DIR = os.path.dirname(APP_DIR)
DATA_DIR = os.path.join(APP_DIR, "data")
TEMP_UPLOADS_DIR = os.path.join(APP_DIR, "temp_uploads")
RESULTS_DIR = os.path.join(DATA_DIR, "results")
LOGS_DIR = os.path.join(APP_DIR, "logs")


def ensure_runtime_dirs():
    for path in (DATA_DIR, TEMP_UPLOADS_DIR, RESULTS_DIR, LOGS_DIR):
        os.makedirs(path, exist_ok=True)


def safe_upload_name(filename):
    """Return a basename-only upload name to avoid accidental path traversal."""
    return os.path.basename(str(filename or "").replace("\\", "/")) or "upload.bin"


def app_relative_path(path):
    """Store paths relative to APP_DIR when possible for machine portability."""
    raw = os.path.abspath(os.path.expanduser(str(path or "")))
    try:
        rel_path = os.path.relpath(raw, APP_DIR)
    except ValueError:
        return raw
    if rel_path == "." or rel_path.startswith(f"..{os.sep}"):
        return raw
    return rel_path.replace("\\", "/")


def resolve_runtime_path(path):
    """
    Resolve historical absolute paths, APP_DIR-relative paths, and project-root
    relative paths. Returns the first existing candidate, otherwise a stable
    APP_DIR-relative candidate.
    """
    raw = str(path or "").strip()
    if not raw:
        return ""

    raw = os.path.expanduser(raw)
    candidates = []

    def add(candidate):
        if not candidate:
            return
        candidate = os.path.normpath(candidate)
        if candidate not in candidates:
            candidates.append(candidate)

    if os.path.isabs(raw):
        add(raw)
    else:
        add(os.path.join(APP_DIR, raw))
        add(os.path.join(PROJECT_DIR, raw))

    basename = os.path.basename(raw)
    if basename:
        add(os.path.join(TEMP_UPLOADS_DIR, basename))
        add(os.path.join(RESULTS_DIR, basename))

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0] if candidates else raw


ensure_runtime_dirs()
