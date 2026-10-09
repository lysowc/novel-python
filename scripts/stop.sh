#!/usr/bin/env bash
# 停止 AI 小说工坊全部服务
# 用法: bash scripts/stop.sh
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

# 1. 按 pid 文件停止
for name in api worker mock; do
  pidfile="${ROOT}/runtime/${name}.pid"
  if [ -f "${pidfile}" ]; then
    pid="$(cat "${pidfile}")"
    if kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null && echo "✔ 已停止 ${name}（pid ${pid}）"
    fi
    rm -f "${pidfile}"
  fi
done

# 2. 兜底：按端口清理残留进程
for port in 8800 8899; do
  pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "${pids}" ]; then
    kill ${pids} 2>/dev/null && echo "✔ 清理端口 ${port} 残留进程"
  fi
done

echo "已停止。"
