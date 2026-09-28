#!/usr/bin/env bash
# Planned-maintenance switch for evistreams.com.
#
#   maintenance.sh on    → every page shows the branded 503; /api/ returns JSON 503
#   maintenance.sh off   → normal service
#   maintenance.sh status
#
# Why this exists: the nginx error_page only covers the Next.js upstream being
# down. Restarting FastAPI alone leaves the app loading fine and then failing its
# own API calls, which surfaces in the UI as a raw 502. This flag makes a planned
# change look planned.
#
# The flag is a file, so it survives an nginx reload and needs no config edit.
set -euo pipefail
FLAG=/var/www/evistream-maintenance/ON
URL=https://evistreams.com

case "${1:-status}" in
  on)
    sudo -n touch "$FLAG"
    sleep 1
    printf 'maintenance ON   page=%s  api=%s\n' \
      "$(curl -s -o /dev/null -w '%{http_code}' "$URL/")" \
      "$(curl -s -o /dev/null -w '%{http_code}' "$URL/api/v1/health" || true)"
    ;;
  off)
    sudo -n rm -f "$FLAG"
    sleep 1
    printf 'maintenance OFF  page=%s  health=%s\n' \
      "$(curl -s -o /dev/null -w '%{http_code}' "$URL/")" \
      "$(curl -s -o /dev/null -w '%{http_code}' "$URL/health")"
    ;;
  status)
    if [ -f "$FLAG" ]; then echo "maintenance is ON"; else echo "maintenance is OFF"; fi
    ;;
  *) echo "usage: $0 {on|off|status}" >&2; exit 2 ;;
esac
