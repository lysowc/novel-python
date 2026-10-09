#!/usr/bin/env bash
# 启动 AI 小说工坊（API + AI 任务 worker + 可选 Mock AI）
# 用法: bash scripts/start.sh          # 全部启动（含 mock）
#       MOCK=0 bash scripts/start.sh   # 不启动 mock（已接真实 AI 时）
#       PORT=8800 bash scripts/start.sh
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Python 解释器：优先 $PYTHON 环境变量，其次项目内 .venv，最后系统 python3
if [ -n "${PYTHON:-}" ]; then
  PY="$PYTHON"
elif [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="python3"
fi
PORT="${PORT:-8800}"
MOCK_PORT="${MOCK_PORT:-8899}"
LOG_DIR="${ROOT}/runtime"
mkdir -p "${LOG_DIR}"
cd "${ROOT}"

alive() { [ -f "${LOG_DIR}/$1.pid" ] && kill -0 "$(cat "${LOG_DIR}/$1.pid")" 2>/dev/null; }

# 1. API
if alive api; then
  echo "⚠ API 已在运行（pid $(cat "${LOG_DIR}/api.pid")）"
elif lsof -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "⚠ 端口 ${PORT} 已被其他程序占用，API 未启动"
else
  nohup "${PY}" -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" >> "${LOG_DIR}/api.log" 2>&1 &
  echo $! > "${LOG_DIR}/api.pid"
  echo "✔ API 已启动: http://127.0.0.1:${PORT}（日志 runtime/api.log）"
fi

# 2. AI 任务消费进程（必须，否则生成/审校任务不会执行）
if alive worker; then
  echo "⚠ worker 已在运行（pid $(cat "${LOG_DIR}/worker.pid")）"
else
  nohup "${PY}" -m app.worker >> "${LOG_DIR}/worker.log" 2>&1 &
  echo $! > "${LOG_DIR}/worker.pid"
  echo "✔ AI 任务 worker 已启动（日志 runtime/worker.log）"
fi

# 3. Mock AI（本地无 API Key 联调用；接真实 AI 后可用 MOCK=0 跳过）
if [ "${MOCK:-1}" = "1" ]; then
  if alive mock; then
    echo "⚠ mock 已在运行（pid $(cat "${LOG_DIR}/mock.pid")）"
  elif lsof -iTCP:"${MOCK_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "⚠ 端口 ${MOCK_PORT} 已被占用，mock 未启动"
  else
    nohup "${PY}" -m uvicorn tests.mock_ai_server:app --port "${MOCK_PORT}" >> "${LOG_DIR}/mock.log" 2>&1 &
    echo $! > "${LOG_DIR}/mock.pid"
    echo "✔ Mock AI 已启动: http://127.0.0.1:${MOCK_PORT}"
  fi
fi

echo
echo "访问: http://127.0.0.1:${PORT}   后台登录 admin / admin123"
