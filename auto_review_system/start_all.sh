#!/bin/bash
echo "🚀 正在并联点火启动万科智能审查系统..."

# Load local secrets for online OCR engines without hardcoding tokens in git.
if [ -f ../.env ]; then
  set -a
  # shellcheck disable=SC1091
  source ../.env
  set +a
fi

if [ -z "$PADDLE_API_TOKEN" ]; then
  echo "⚠️ 未检测到 PADDLE_API_TOKEN，在线 PaddleOCR 引擎将不可用。"
fi

# 1. Start Streamlit frontend
echo "1️⃣ 唤醒 v3 专家前端工作台..."
../.venv/bin/streamlit run app.py &
UI_PID=$!

# 2. Start v3 review Worker
echo "2️⃣ 启动 v3 AI 主审异步 Worker..."
export PYTHONUNBUFFERED=1
../.venv/bin/python agent_worker.py >> logs/agent_worker.log 2>&1 &
WORKER_PID=$!

# Handle shutdown cleanly
trap "echo '🔴 监测到退出指令，已为您安全关闭微服务进程。'; kill $WORKER_PID $UI_PID 2>/dev/null; exit" SIGINT SIGTERM

echo "✅ 系统全维度进入运作状态。终端按 Ctrl+C 可以同时熄火所有系统微服务。"

# 3. Health check loop — 每 30 秒检查一次进程存活状态，崩溃自动拉起
HEALTH_CHECK_INTERVAL=${HEALTH_CHECK_INTERVAL:-30}
mkdir -p logs

while true; do
    sleep "$HEALTH_CHECK_INTERVAL"

    # Check Worker process
    if ! kill -0 $WORKER_PID 2>/dev/null; then
        echo "[$(date '+%H:%M:%S')] ⚠️ v3 Review Worker (PID=$WORKER_PID) 已崩溃，正在自动拉起..." | tee -a logs/healthcheck.log
        ../.venv/bin/python agent_worker.py >> logs/agent_worker.log 2>&1 &
        WORKER_PID=$!
        echo "[$(date '+%H:%M:%S')] ✅ v3 Review Worker 已重新启动 (PID=$WORKER_PID)" | tee -a logs/healthcheck.log
    fi

    # Check UI process
    if ! kill -0 $UI_PID 2>/dev/null; then
        echo "[$(date '+%H:%M:%S')] ⚠️ Streamlit UI (PID=$UI_PID) 已崩溃，正在自动拉起..." | tee -a logs/healthcheck.log
        ../.venv/bin/streamlit run app.py &
        UI_PID=$!
        echo "[$(date '+%H:%M:%S')] ✅ Streamlit UI 已重新启动 (PID=$UI_PID)" | tee -a logs/healthcheck.log
    fi
done
