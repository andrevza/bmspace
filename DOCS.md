# BMS Pace

## Configuration

Example App configuration:

```yaml
mqtt_host: "10.0.0.132"
mqtt_port: 1883
mqtt_user: "mqtt-users"
mqtt_password: "mqtt-users"
mqtt_ha_discovery: true
mqtt_ha_discovery_topic: "homeassistant"
mqtt_base_topic: "bmspace"
connection_type: "IP"
bms_ip: "10.0.0.161"
bms_port: 5000
bms_serial: "/dev/ttyUSB0"
scan_interval: 5
debug_output: 0
```

## Runtime and security notes
- This App runs as `root` in the container to keep Serial mode (`/dev/ttyUSB*`) reliable across Home Assistant host setups.
- Even with root runtime, the App is configured with no extra Linux privileges (`privileged: []`) and does not require host networking.

### Option: `mqtt_host`
Hostname or IP of the MQTT broker.
Default: `10.0.0.132`

### Option: `mqtt_port`
MQTT broker TCP port.
Must be a valid port number (`1`-`65535`).
Default: `1883`

### Option: `mqtt_client_id` (optional)
Explicit MQTT client ID.
If empty, a stable default is generated (`bmspace-<hostname>`).
Default: empty (auto-generated)

### Option: `mqtt_user`
MQTT username.
Default: `mqtt-users`

### Option: `mqtt_password`
MQTT password.
In Home Assistant this uses a password field, so the value is masked in the UI.
Default: `mqtt-users`

### Option: `mqtt_ha_discovery`
Enable Home Assistant MQTT discovery entity publishing.
Default: `true`

### Option: `mqtt_discovery_cleanup_startup` (optional)
Remove stale retained MQTT discovery config topics once at startup.
Keep enabled to clean up entities that no longer exist after topic/entity changes.
Default: `true`

### Option: `mqtt_ha_discovery_topic`
Home Assistant MQTT discovery prefix topic.  
Default: `homeassistant`

### Option: `mqtt_base_topic`
Base topic used for runtime telemetry and availability.  
Default: `bmspace`

### Option: `connection_type`
BMS transport type.  
Allowed values: `IP` or `Serial`
Default: `IP`

### Option: `bms_ip`
BMS TCP bridge/server IP when `connection_type: IP`.
Not used when `connection_type: Serial`.
Default: `10.0.0.161`

### Option: `bms_port`
BMS TCP bridge/server port when `connection_type: IP`.
Must be a valid port number (`1`-`65535`).
Not used when `connection_type: Serial`.
Default: `5000`

### Option: `bms_serial`
Serial device path when `connection_type: Serial`.  
Example: `/dev/ttyUSB0`
Not used when `connection_type: IP`.
Default: `/dev/ttyUSB0`

### Option: `scan_interval`
Polling interval in seconds between telemetry cycles.
Default: `5`

### Option: `debug_output`
Debug verbosity level:
- `0`: minimal output
- `1`: include non-fatal parse/debug messages and discovery entity logging
- `2`/`3`: increasingly verbose protocol-level debug output
Default: `0`

### Option: `bms_connect_retries` (optional)
Number of retry attempts when connecting/reconnecting to the BMS.
Default when omitted: `5` (minimum `1`)

### Option: `bms_connect_retry_delay` (optional)
Delay in seconds between BMS connection retry attempts.
Default when omitted: `5` seconds (minimum `1`)

### Option: `force_pack_offset` (optional)
Manual byte offset between multi-pack analog blocks for devices that report
misaligned payloads. Keep `0` unless multi-pack parsing is misaligned.
Default: `0`

### Option: `packs_to_read` (optional)
Cap number of packs parsed/published each cycle.
Use `0` for auto-detect/full payload.
Default: `0`

### Option: `zero_pad_number_cells` (optional)
Zero-padding width for cell numbers in MQTT topic/entity IDs.
Example: `2` makes `cell_1` become `cell_01`.
Default when omitted: `0` (no padding)

### Option: `zero_pad_number_packs` (optional)
Zero-padding width for pack numbers in MQTT topic/entity IDs.
Example: `2` makes `pack_1` become `pack_01`.
Default when omitted: `0` (no padding)

## Notes

- Use `connection_type: IP` for TCP-to-RS232 bridges.
- Use `connection_type: Serial` for direct serial adapters passed through to
  Home Assistant.
- `bms_ip`/`bms_port` are required only for `IP`; `bms_serial` is required only
  for `Serial`.
- If discovery is enabled, entities are published to the configured discovery
  topic and state topics under `mqtt_base_topic`.
- Changing zero-pad settings changes MQTT topic/entity naming and can create
  new Home Assistant entities.
