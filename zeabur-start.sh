#!/bin/sh
set -eu

mkdir -p /data/uploads

export RELAY_PORT="${PORT:-8080}"
export LOOP_PORT="${LOOP_PORT:-3020}"
export RELAY_DEFAULT_BRAIN="${RELAY_DEFAULT_BRAIN:-loop}"
export RELAY_LOOP_INGEST_URL="${RELAY_LOOP_INGEST_URL:-http://127.0.0.1:${LOOP_PORT}/loop/ingest}"
export RELAY_URL="${RELAY_URL:-http://127.0.0.1:${RELAY_PORT}}"
export HOME_STATE_PORT="${HOME_STATE_PORT:-3025}"
export HOME_STATE_FILE="${HOME_STATE_FILE:-/data/home_state.json}"

python /app/examples/api_loop.py &
loop_pid=$!

python /app/examples/home_state_mcp.py &
mcp_pid=$!

# ── Drivesoid 情绪 sidecar(同容器回环 127.0.0.1:24601,不对外暴露)─────────
# 首次启动克隆到 /data(持久卷)并生成配置;之后随容器常驻,崩溃自拉起。
# 不在下方看门狗的存活检查里:它挂了只影响情绪注入,不能拖垮聊天主链路。
if [ "${DRIVES_ENABLED:-1}" != "0" ]; then
  DRIVES_DIR="${DRIVES_DIR:-/data/Drivesoid}"
  (
    set -e
    if [ ! -d "$DRIVES_DIR/.git" ]; then
      rm -rf "$DRIVES_DIR"
      git clone --depth 1 https://github.com/A1batr055/Drivesoid.git "$DRIVES_DIR"
    fi
    cd "$DRIVES_DIR"
    [ -d node_modules ] || npm install --omit=dev
    if [ ! -f drives.config.json ]; then
      # 首次启动按环境变量生成配置;之后想改直接编辑 /data/Drivesoid/drives.config.json
      cat > drives.config.json <<EOF
{
  "persona": { "name": "${DRIVES_PERSONA_NAME:-阿克}" },
  "user": { "name": "${DRIVES_USER_NAME:-你}" },
  "relation": "${DRIVES_RELATION:-romantic}",
  "timezone_offset_hours": ${DRIVES_TZ_OFFSET:-8},
  "classifier": {
    "endpoint": "${DRIVES_CLASSIFIER_ENDPOINT:-https://api.deepseek.com}",
    "model": "${DRIVES_CLASSIFIER_MODEL:-deepseek-v4-flash}",
    "api_key_env": "DRIVES_API_KEY"
  },
  "server": { "port": 24601 }
}
EOF
    fi
    [ -n "${DRIVES_API_KEY:-}" ] || echo "[drivesoid] WARN: DRIVES_API_KEY 未设置,情绪分类器不会工作(去 Zeabur 环境变量里加)" >&2
    while true; do
      npm start || true
      echo "[drivesoid] exited, restart in 5s" >&2
      sleep 5
    done
  ) &
  drives_pid=$!
fi

cd /app/backend
# --no-access-log: attachment/SSE URLs carry ?token=<RELAY_SECRET>;
# uvicorn's access log would persist the master key in plaintext logs.
uvicorn app:app --host 0.0.0.0 --port "$RELAY_PORT" --no-access-log &
relay_pid=$!

cleanup() {
  kill "$loop_pid" 2>/dev/null || true
  kill "$mcp_pid" 2>/dev/null || true
  [ -n "${drives_pid:-}" ] && kill "$drives_pid" 2>/dev/null || true
  kill "$relay_pid" 2>/dev/null || true
}

trap cleanup INT TERM EXIT

while kill -0 "$loop_pid" 2>/dev/null && kill -0 "$mcp_pid" 2>/dev/null && kill -0 "$relay_pid" 2>/dev/null; do
  sleep 1
done

exit 1