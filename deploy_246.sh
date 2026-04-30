#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE_HOST="${REMOTE_HOST:-lifeos-server}"
REMOTE_ROOT="${REMOTE_ROOT:-/home/xionglei/vanke_chk_list}"
SYNC_ENV="${SYNC_ENV:-1}"
SYNC_VECTOR_DB="${SYNC_VECTOR_DB:-1}"
FORCE_REBUILD_VECTOR_DB="${FORCE_REBUILD_VECTOR_DB:-0}"

echo "Deploying repository to ${REMOTE_HOST}:${REMOTE_ROOT} ..."

ssh "$REMOTE_HOST" "mkdir -p '$REMOTE_ROOT'"

rsync -az --delete \
  --exclude='.git/' \
  --exclude='.venv/' \
  --exclude='.agents/' \
  --exclude='.claude/' \
  --exclude='.env' \
  --exclude='.env.*' \
  --exclude='__pycache__/' \
  --exclude='logs/' \
  --exclude='*.log' \
  --exclude='vector_db/' \
  --exclude='vector_db_storage/' \
  --exclude='temp_uploads/' \
  --exclude='原始材料/' \
  --exclude='auto_review_system/logs/' \
  --exclude='auto_review_system/data/results/' \
  --exclude='auto_review_system/data/audit_queue.db' \
  --exclude='auto_review_system/data/pageindex_ocr_cache/' \
  --exclude='auto_review_system/temp_uploads/' \
  --exclude='auto_review_system/vector_db_storage/' \
  "$ROOT_DIR/" "${REMOTE_HOST}:${REMOTE_ROOT}/"

if [ "$SYNC_ENV" = "1" ] && [ -f "$ROOT_DIR/.env" ]; then
  echo "Syncing .env ..."
  rsync -az "$ROOT_DIR/.env" "${REMOTE_HOST}:${REMOTE_ROOT}/.env"
fi

if [ "$SYNC_VECTOR_DB" = "1" ] && [ -d "$ROOT_DIR/auto_review_system/vector_db_storage" ]; then
  echo "Syncing vector_db_storage ..."
  rsync -az --delete \
    "$ROOT_DIR/auto_review_system/vector_db_storage/" \
    "${REMOTE_HOST}:${REMOTE_ROOT}/auto_review_system/vector_db_storage/"
fi

echo "Restarting remote services ..."
ssh "$REMOTE_HOST" "cd '$REMOTE_ROOT' && FORCE_REBUILD_VECTOR_DB='$FORCE_REBUILD_VECTOR_DB' bash ./fix_remote.sh"

echo "Deployment completed."
