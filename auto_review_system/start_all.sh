#!/bin/bash
echo "🚀 正在并联点火启动万科智能审查系统..."

# 1. Start vector API in the background
echo "1️⃣ 启动底层规则向量引擎接口服务 (Port:8001)..."
../.venv/bin/python start_vector_api.py &
API_PID=$!

# Wait for API to initialize
sleep 3

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

# 2. Start Streamlit frontend
echo "2️⃣ 唤醒专家前端总控大屏..."
../.venv/bin/streamlit run app.py &
UI_PID=$!

# 3. Start Agent Worker
echo "3️⃣ 启动异步审查死任务处理列车..."
export PYTHONUNBUFFERED=1
../.venv/bin/python agent_worker.py >> logs/agent_worker.log 2>&1 &
WORKER_PID=$!

# Handle shutdown cleanly
trap "echo '🔴 监测到退出指令，已为您安全关闭微服务进程。'; kill $API_PID $WORKER_PID $UI_PID 2>/dev/null; exit" SIGINT SIGTERM

echo "✅ 系统全维度进入运作状态。终端按 Ctrl+C 可以同时熄火所有系统微服务。"

# 4. Health check loop — 每 30 秒检查一次进程存活状态，崩溃自动拉起
HEALTH_CHECK_INTERVAL=${HEALTH_CHECK_INTERVAL:-30}
mkdir -p logs

while true; do
    sleep "$HEALTH_CHECK_INTERVAL"

    # Check API process
    if ! kill -0 $API_PID 2>/dev/null; then
        echo "[$(date '+%H:%M:%S')] ⚠️ Vector API (PID=$API_PID) 已崩溃，正在自动拉起..." | tee -a logs/healthcheck.log
        ../.venv/bin/python start_vector_api.py &
        API_PID=$!
        echo "[$(date '+%H:%M:%S')] ✅ Vector API 已重新启动 (PID=$API_PID)" | tee -a logs/healthcheck.log
    fi

    # Check Worker process
    if ! kill -0 $WORKER_PID 2>/dev/null; then
        echo "[$(date '+%H:%M:%S')] ⚠️ Agent Worker (PID=$WORKER_PID) 已崩溃，正在自动拉起..." | tee -a logs/healthcheck.log
        ../.venv/bin/python agent_worker.py >> logs/agent_worker.log 2>&1 &
        WORKER_PID=$!
        echo "[$(date '+%H:%M:%S')] ✅ Agent Worker 已重新启动 (PID=$WORKER_PID)" | tee -a logs/healthcheck.log
    fi

    # Check UI process
    if ! kill -0 $UI_PID 2>/dev/null; then
        echo "[$(date '+%H:%M:%S')] ⚠️ Streamlit UI (PID=$UI_PID) 已崩溃，正在自动拉起..." | tee -a logs/healthcheck.log
        ../.venv/bin/streamlit run app.py &
        UI_PID=$!
        echo "[$(date '+%H:%M:%S')] ✅ Streamlit UI 已重新启动 (PID=$UI_PID)" | tee -a logs/healthcheck.log
    fi
done
