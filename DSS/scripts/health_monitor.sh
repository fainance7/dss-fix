#!/bin/sh
# DSS health monitor — периодически проверяет доступность сервисов и шлёт алерт в
# Telegram при падении/восстановлении (НФТ: доступность 99.5%, алерты в Telegram).
#
# Запускается как compose-сервис `healthmon` (см. docker-compose.yml) в сети
# dss-net. Проверяет ВНУТРЕННИЕ health-эндпоинты (не через внешний прокси).
#
# Env:
#   HEALTHMON_INTERVAL   — период проверки, сек (по умолчанию 60)
#   HEALTHMON_FAILS      — сколько подряд неудач до алерта (по умолчанию 2)
#   TELEGRAM_BOT_TOKEN   — токен бота (если пусто — только лог, без Telegram)
#   TELEGRAM_CHAT_ID     — чат/канал для алертов
#   HEALTHMON_TARGETS    — список "имя=URL" через ; (иначе дефолт ниже)
set -eu

INTERVAL="${HEALTHMON_INTERVAL:-60}"
MAX_FAILS="${HEALTHMON_FAILS:-2}"

# Дефолтные цели: имя=URL (внутренние адреса на dss-net). Up = HTTP < 400.
DEFAULT_TARGETS="backend=http://dss-backend:8000/health;ai-service=http://dss-ai-service:8001/health;frontend=http://dss-frontend:3000;titiler=http://dss-titiler:8000/healthz"
TARGETS="${HEALTHMON_TARGETS:-$DEFAULT_TARGETS}"

log() { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [healthmon] $*"; }

telegram() {
  # $1 = текст сообщения
  [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ] || return 0
  curl -s -m 10 -o /dev/null \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=$1" \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" || true
}

# Состояние по каждой цели храним в файлах /tmp/state_<имя> (up|down) и /tmp/fails_<имя>.
STATE_DIR="/tmp/healthmon"
mkdir -p "$STATE_DIR"

check_one() {
  # $1 name, $2 url → 0 если up
  code=$(curl -s -m 8 -o /dev/null -w '%{http_code}' "$2" 2>/dev/null || echo 000)
  [ "$code" -ge 200 ] 2>/dev/null && [ "$code" -lt 400 ] 2>/dev/null
}

log "старт: interval=${INTERVAL}s max_fails=${MAX_FAILS} telegram=$([ -n "${TELEGRAM_BOT_TOKEN:-}" ] && echo on || echo off)"
telegram "🟢 DSS health-monitor запущен ($(date -u '+%H:%M UTC'))"

while true; do
  IFS=';'
  for pair in $TARGETS; do
    name="${pair%%=*}"; url="${pair#*=}"
    [ -n "$name" ] || continue
    sf="$STATE_DIR/state_$name"; ff="$STATE_DIR/fails_$name"
    prev="$(cat "$sf" 2>/dev/null || echo up)"
    fails="$(cat "$ff" 2>/dev/null || echo 0)"
    if check_one "$name" "$url"; then
      if [ "$prev" = "down" ]; then
        log "ВОССТАНОВЛЕН: $name ($url)"
        telegram "✅ DSS: сервис «$name» восстановлен ($url)"
      fi
      echo up > "$sf"; echo 0 > "$ff"
    else
      fails=$((fails + 1)); echo "$fails" > "$ff"
      log "НЕДОСТУПЕН ($fails/$MAX_FAILS): $name ($url)"
      if [ "$fails" -ge "$MAX_FAILS" ] && [ "$prev" != "down" ]; then
        echo down > "$sf"
        telegram "🔴 DSS: сервис «$name» НЕДОСТУПЕН ($url) — $fails проверок подряд"
      fi
    fi
  done
  unset IFS
  sleep "$INTERVAL"
done
