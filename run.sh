#!/usr/bin/with-contenv bashio
set -e

if command -v bashio::log.info >/dev/null 2>&1 && command -v bashio::color.green >/dev/null 2>&1; then
  bashio::log.info "$(bashio::color.green 'Hello BMS Pace')"
else
  echo "Hello BMS Pace"
fi

cd /workdir
exec python3 -u ./bms.py #"$@"
