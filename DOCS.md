# BMS Pace

## Configuration

Example add-on configuration:

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
bms_connect_retries: 5
bms_connect_retry_delay: 5
force_pack_offset: 0
```

### Option: `mqtt_host`
Hostname or IP of the MQTT broker.

### Option: `mqtt_port`
MQTT broker TCP port.
Must be a valid port number (`1`-`65535`).

### Option: `mqtt_user`
MQTT username.

### Option: `mqtt_password`
MQTT password.
In Home Assistant this uses a password field, so the value is masked in the UI.

### Option: `mqtt_ha_discovery`
Enable Home Assistant MQTT discovery entity publishing.

### Option: `mqtt_ha_discovery_topic`
Home Assistant MQTT discovery prefix topic.  
Default: `homeassistant`

### Option: `mqtt_base_topic`
Base topic used for runtime telemetry and availability.  
Default: `bmspace`

### Option: `connection_type`
BMS transport type.  
Allowed values: `IP` or `Serial`

### Option: `bms_ip`
BMS TCP bridge/server IP when `connection_type: IP`.

### Option: `bms_port`
BMS TCP bridge/server port when `connection_type: IP`.
Must be a valid port number (`1`-`65535`).

### Option: `bms_serial`
Serial device path when `connection_type: Serial`.  
Example: `/dev/ttyUSB0`

### Option: `scan_interval`
Polling interval in seconds between telemetry cycles.

### Option: `debug_output`
Debug verbosity level:
- `0`: minimal output
- `1`: include non-fatal parse/debug messages and discovery entity logging
- `2`/`3`: increasingly verbose protocol-level debug output

### Option: `bms_connect_retries` (optional)
Number of retry attempts when connecting/reconnecting to the BMS.

### Option: `bms_connect_retry_delay` (optional)
Delay in seconds between BMS connection retry attempts.

### Option: `force_pack_offset` (optional)
Manual byte offset between multi-pack analog blocks for devices that report
misaligned payloads. Keep `0` unless multi-pack parsing is offset.

## Notes

- Use `connection_type: IP` for TCP-to-RS232 bridges.
- Use `connection_type: Serial` for direct serial adapters passed through to
  Home Assistant.
- If discovery is enabled, entities are published to the configured discovery
  topic and state topics under `mqtt_base_topic`.
