#!/usr/bin/env bash
# 停止 AI 小说工坊全部服务
# 用法: bash scripts/stop.sh
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

# 1. 按 pid 文件停止（api / mock / 多进程 worker）
for name in api mock; do
  pidfile="${ROOT}/runtime/${name}.pid"
  if [ -f "${pidfile}" ]; then
    pid="$(cat "${pidfile}")"
    if kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null && echo "✔ 已停止 ${name}（pid ${pid}）"
    fi
    rm -f "${pidfile}"
  fi
done

# worker 多进程（worker.1.pid / worker.2.pid ...）
for pidfile in "${ROOT}"/runtime/worker.*.pid; do
  [ -f "${pidfile}" ] || continue
  pid="$(cat "${pidfile}")"
  if kill -0 "${pid}" 2>/dev/null; then
    kill "${pid}" 2>/dev/null && echo "✔ 已停止 worker（pid ${pid}）"
  fi
  rm -f "${pidfile}"
done

# 兼容旧的单进程 worker.pid
if [ -f "${ROOT}/runtime/worker.pid" ]; then
  pid="$(cat "${ROOT}/runtime/worker.pid")"
  kill -0 "${pid}" 2>/dev/null && kill "${pid}" 2>/dev/null && echo "✔ 已停止旧 worker（pid ${pid}）"
  rm -f "${ROOT}/runtime/worker.pid"
fi

# 2. 兜底：按端口清理残留进程
for port in 8800 8899; do
  pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "${pids}" ]; then
    kill ${pids} 2>/dev/null && echo "✔ 清理端口 ${port} 残留进程"
  fi
done

echo "已停止。"
