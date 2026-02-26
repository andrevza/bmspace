#!/usr/bin/with-contenv bashio
set -e

bashio::log.info "$(bashio::color.green 'Hello BMS Pace')"

cd /workdir
exec python3 -u ./bms.py #"$@"
