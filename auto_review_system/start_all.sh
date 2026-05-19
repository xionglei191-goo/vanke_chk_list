#!/bin/bash

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
RUN_DIR="$SCRIPT_DIR/run"
UI_PID_FILE="$RUN_DIR/streamlit.pid"
WORKER_PID_FILE="$RUN_DIR/agent_worker.pid"
HEALTH_PID_FILE="$RUN_DIR/healthcheck.pid"

mkdir -p "$LOG_DIR" "$RUN_DIR"

if [ -f "$PROJECT_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env"
  set +a
fi

if [ -z "${PADDLE_API_TOKEN:-}" ]; then
  echo "⚠️ 未检测到 PADDLE_API_TOKEN，在线 PaddleOCR 引擎将不可用。"
fi

export PYTHONUNBUFFERED=1
export STREAMLIT_SERVER_HEADLESS=true
export STREAMLIT_SERVER_FILE_WATCHER_TYPE=none
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

is_running_pid() {
  local pid="$1"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

read_pid_file() {
  local pid_file="$1"
  if [ -f "$pid_file" ]; then
    tr -d '[:space:]' < "$pid_file"
  fi
}

pid_is_cmd() {
  local pid="$1"
  local pattern="$2"
  local cmdline
  if ! is_running_pid "$pid"; then
    return 1
  fi
  cmdline="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  [[ "$cmdline" == *"$pattern"* ]]
}

cleanup_pid_file_if_stale() {
  local pid_file="$1"
  local pattern="$2"
  local pid
  pid="$(read_pid_file "$pid_file")"
  if [ -n "$pid" ] && ! pid_is_cmd "$pid" "$pattern"; then
    rm -f "$pid_file"
  fi
}

start_ui() {
  local pid
  cleanup_pid_file_if_stale "$UI_PID_FILE" "streamlit run app.py"
  pid="$(read_pid_file "$UI_PID_FILE")"
  if pid_is_cmd "$pid" "streamlit run app.py"; then
    echo "ℹ️ Streamlit UI 已在运行 (PID=$pid)"
    return 0
  fi

  echo "1️⃣ 唤醒 v3 专家前端工作台..."
  setsid "$PROJECT_ROOT/.venv/bin/streamlit" run app.py \
    --server.headless true \
    --server.fileWatcherType none \
    >> "$LOG_DIR/streamlit.out" 2>&1 < /dev/null &
  pid=$!
  echo "$pid" > "$UI_PID_FILE"
  sleep 2
  if pid_is_cmd "$pid" "streamlit run app.py"; then
    echo "✅ Streamlit UI 已启动 (PID=$pid)"
    return 0
  fi

  echo "❌ Streamlit UI 启动失败，请查看 $LOG_DIR/streamlit.out"
  rm -f "$UI_PID_FILE"
  return 1
}

start_worker() {
  local pid
  cleanup_pid_file_if_stale "$WORKER_PID_FILE" "python agent_worker.py"
  pid="$(read_pid_file "$WORKER_PID_FILE")"
  if pid_is_cmd "$pid" "python agent_worker.py"; then
    echo "ℹ️ v3 Review Worker 已在运行 (PID=$pid)"
    return 0
  fi

  echo "2️⃣ 启动 v3 AI 主审异步 Worker..."
  setsid "$PROJECT_ROOT/.venv/bin/python" agent_worker.py >> "$LOG_DIR/agent_worker.out" 2>&1 < /dev/null &
  pid=$!
  echo "$pid" > "$WORKER_PID_FILE"
  sleep 2
  if pid_is_cmd "$pid" "python agent_worker.py"; then
    echo "✅ v3 Review Worker 已启动 (PID=$pid)"
    return 0
  fi

  echo "❌ v3 Review Worker 启动失败，请查看 $LOG_DIR/agent_worker.out"
  rm -f "$WORKER_PID_FILE"
  return 1
}

stop_process() {
  local pid_file="$1"
  local label="$2"
  local pattern="$3"
  local pid
  pid="$(read_pid_file "$pid_file")"
  if ! pid_is_cmd "$pid" "$pattern"; then
    rm -f "$pid_file"
    echo "ℹ️ $label 未在运行"
    return 0
  fi

  kill "$pid" 2>/dev/null || true
  for _ in 1 2 3 4 5; do
    if ! is_running_pid "$pid"; then
      break
    fi
    sleep 1
  done
  if is_running_pid "$pid"; then
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$pid_file"
  echo "🛑 $label 已停止"
}

healthcheck_loop() {
  local interval="${HEALTH_CHECK_INTERVAL:-30}"
  while true; do
    sleep "$interval"

    cleanup_pid_file_if_stale "$UI_PID_FILE" "streamlit run app.py"
    cleanup_pid_file_if_stale "$WORKER_PID_FILE" "python agent_worker.py"

    local ui_pid worker_pid
    ui_pid="$(read_pid_file "$UI_PID_FILE")"
    worker_pid="$(read_pid_file "$WORKER_PID_FILE")"

    if ! pid_is_cmd "$worker_pid" "python agent_worker.py"; then
      echo "[$(date '+%F %T')] ⚠️ v3 Review Worker 已退出，正在自动拉起..." | tee -a "$LOG_DIR/healthcheck.log"
      start_worker | tee -a "$LOG_DIR/healthcheck.log"
    fi

    if ! pid_is_cmd "$ui_pid" "streamlit run app.py"; then
      echo "[$(date '+%F %T')] ⚠️ Streamlit UI 已退出，正在自动拉起..." | tee -a "$LOG_DIR/healthcheck.log"
      start_ui | tee -a "$LOG_DIR/healthcheck.log"
    fi
  done
}

start_healthcheck() {
  local pid
  cleanup_pid_file_if_stale "$HEALTH_PID_FILE" "start_all.sh daemon"
  pid="$(read_pid_file "$HEALTH_PID_FILE")"
  if pid_is_cmd "$pid" "start_all.sh daemon"; then
    echo "ℹ️ 守护巡检已在运行 (PID=$pid)"
    return 0
  fi

  setsid "$SCRIPT_DIR/start_all.sh" daemon >> "$LOG_DIR/healthcheck.out" 2>&1 < /dev/null &
  pid=$!
  echo "$pid" > "$HEALTH_PID_FILE"
  echo "✅ 守护巡检已启动 (PID=$pid)"
}

stop_healthcheck() {
  stop_process "$HEALTH_PID_FILE" "守护巡检" "start_all.sh daemon"
}

show_status() {
  cleanup_pid_file_if_stale "$UI_PID_FILE" "streamlit run app.py"
  cleanup_pid_file_if_stale "$WORKER_PID_FILE" "python agent_worker.py"
  cleanup_pid_file_if_stale "$HEALTH_PID_FILE" "start_all.sh daemon"

  local ui_pid worker_pid health_pid
  ui_pid="$(read_pid_file "$UI_PID_FILE")"
  worker_pid="$(read_pid_file "$WORKER_PID_FILE")"
  health_pid="$(read_pid_file "$HEALTH_PID_FILE")"

  if pid_is_cmd "$ui_pid" "streamlit run app.py"; then
    echo "UI: running (PID=$ui_pid) http://127.0.0.1:8501"
  else
    echo "UI: stopped"
  fi

  if pid_is_cmd "$worker_pid" "python agent_worker.py"; then
    echo "Worker: running (PID=$worker_pid)"
  else
    echo "Worker: stopped"
  fi

  if pid_is_cmd "$health_pid" "start_all.sh daemon"; then
    echo "Healthcheck: running (PID=$health_pid)"
  else
    echo "Healthcheck: stopped"
  fi
}

run_foreground() {
  echo "🚀 正在并联点火启动万科智能审查系统..."
  start_ui || return 1
  start_worker || return 1

  local ui_pid worker_pid
  ui_pid="$(read_pid_file "$UI_PID_FILE")"
  worker_pid="$(read_pid_file "$WORKER_PID_FILE")"

  trap 'echo "🔴 监测到退出指令，已为您安全关闭微服务进程。"; stop_healthcheck >/dev/null 2>&1 || true; stop_process "$WORKER_PID_FILE" "v3 Review Worker" "python agent_worker.py" >/dev/null 2>&1 || true; stop_process "$UI_PID_FILE" "Streamlit UI" "streamlit run app.py" >/dev/null 2>&1 || true; exit' SIGINT SIGTERM

  echo "✅ 系统全维度进入运作状态。终端按 Ctrl+C 可以同时熄火所有系统微服务。"
  while true; do
    sleep 30
    if ! pid_is_cmd "$ui_pid" "streamlit run app.py" || ! pid_is_cmd "$worker_pid" "python agent_worker.py"; then
      echo "⚠️ 检测到关键进程退出，请运行 ./start_all.sh status 或 ./start_all.sh start 检查。"
      return 1
    fi
  done
}

run_start() {
  echo "🚀 正在并联点火启动万科智能审查系统..."
  start_ui || return 1
  start_worker || return 1
  start_healthcheck
  echo "✅ 系统已在后台运行。可访问 http://127.0.0.1:8501"
}

case "${1:-start}" in
  start)
    run_start
    ;;
  daemon)
    healthcheck_loop
    ;;
  foreground)
    run_foreground
    ;;
  stop)
    stop_healthcheck
    stop_process "$WORKER_PID_FILE" "v3 Review Worker" "python agent_worker.py"
    stop_process "$UI_PID_FILE" "Streamlit UI" "streamlit run app.py"
    ;;
  restart)
    "$SCRIPT_DIR/start_all.sh" stop
    "$SCRIPT_DIR/start_all.sh" start
    ;;
  status)
    show_status
    ;;
  *)
    echo "用法: ./start_all.sh [start|foreground|daemon|stop|restart|status]"
    exit 1
    ;;
esac
