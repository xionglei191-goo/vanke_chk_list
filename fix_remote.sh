#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$ROOT_DIR/auto_review_system"
VENV_PY="$ROOT_DIR/.venv/bin/python"
FORCE_REBUILD_VECTOR_DB="${FORCE_REBUILD_VECTOR_DB:-0}"

cd "$APP_DIR"
mkdir -p logs

echo "Killing previous hanging processes..."
pkill -9 -f 'start_all.sh' || true
pkill -9 -f 'agent_worker.py' || true
pkill -9 -f 'streamlit run app.py' || true
pkill -9 -f 'start_vector_api.py' || true
sleep 2

if [ "$FORCE_REBUILD_VECTOR_DB" = "1" ]; then
  echo "Rebuilding Vector DB..."
  rm -rf vector_db_storage
  "$VENV_PY" -c 'from rag_engine.vector_store import init_vector_db; init_vector_db(force=True)'
else
  echo "Keeping existing Vector DB cache..."
  if [ ! -f vector_db_storage/chroma.sqlite3 ]; then
    echo "Vector DB cache missing, building once..."
    "$VENV_PY" -c 'from rag_engine.vector_store import init_vector_db; init_vector_db(force=True)'
  fi
fi

chmod -R u+rwX vector_db_storage 2>/dev/null || true

echo "Restarting ecosystem..."
nohup ./start_all.sh > startup.log 2>&1 < /dev/null &
echo "Done."
