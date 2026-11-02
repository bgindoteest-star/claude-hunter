#!/usr/bin/env bash
# setup_cron.sh — pasang entri cron untuk claude-hunter.
#   ./setup_cron.sh            install/update crontab
#   ./setup_cron.sh --print    cetak entri saja, tidak mengubah crontab
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$DIR/.venv/bin/python3"
HUNTER="$DIR/hunter.py"
LOG="$DIR/hunter.log"

if [[ ! -x "$PY" ]]; then
  echo "ERROR: $PY tidak ada. Jalankan dulu: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

# interval dari .env (default 30 menit)
INTERVAL=30
if [[ -f "$DIR/.env" ]]; then
  val="$(grep -E '^CHECK_INTERVAL_MINUTES=' "$DIR/.env" | tail -1 | cut -d= -f2 | tr -d '[:space:]')"
  [[ -n "$val" ]] && INTERVAL="$val"
fi

ENTRY="*/${INTERVAL} * * * * ${PY} ${HUNTER} >> ${LOG} 2>&1"

if [[ "${1:-}" == "--print" ]]; then
  echo "$ENTRY"
  exit 0
fi

( crontab -l 2>/dev/null | grep -v -F "$HUNTER" || true; echo "$ENTRY" ) | crontab -
echo "cron terpasang:"
echo "$ENTRY"
crontab -l | grep -F "$HUNTER"
