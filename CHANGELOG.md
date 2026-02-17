# Changelog

## v3.0.0
- Improved MQTT and BMS connection resilience with explicit retry handling and safer startup/reconnect behavior.
- Hardened protocol parsing for analog and warning payloads with clearer truncation/error handling and safer multi-pack alignment.
- Added graceful shutdown handling (SIGTERM/SIGINT) with explicit shutdown logs for start, BMS disconnect, and MQTT disconnect.
- Extended optional configuration with pack/cell zero-padding support for MQTT topic/entity naming.
- Made transport settings mode-aware: `IP` requires `bms_ip`/`bms_port`, `Serial` requires `bms_serial`.
- Marked warning/protection/status discovery entities as Home Assistant diagnostic entities.
- Expanded automated tests, including coverage for zero-padding behavior and discovery topic/entity naming.
- Updated documentation: clarified App option defaults and aligned README configuration guidance for easier setup.

## v2.2.0
- Added a calculated cell maximum voltage difference (highest cell voltage - smallest cell voltage).
- Rewrote Dockerfile to cache library dependencies to speed up future builds. (thanks jpmeijers)
- Added `docker-compose.yaml` to run using docker compose with auto-restarting. (thanks jpmeijers)
- Bugfix: removed spaces from serial numbers to prevent HA unique identifiers with spaces.

## v2.1.0
- Fixed multiple packs not parsed correctly in some instances.
- Abbreviated some warning info to help prevent exceeding HA character limits.

## v2.0.4
- Balance data should be base 16, not 8.

## v2.0.3
- Possible bugfix for larger banks reading incorrect analog data.

## v2.0.2
- Naming fixes.
- Possible fix for native and USB serial devices.

## v2.0.1
- Bug fixes.

## v2.0.0
- Major rewrite using the official Pace RS232 Protocol Definition.
- Breaking change: most data prefixed with pack number (multi-battery support).
- Temperature values retrieved without names.
- In one known mapping: first 4 are cell temperatures, temp 5 is MOSFET, temp 6 is ambient/environment.

## Known / possible issues
- Overall Pack data under the root MQTT topic may follow the first battery in the pack.
