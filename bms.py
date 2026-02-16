import paho.mqtt.client as mqtt
import socket
import time
import yaml
import os
import json
import serial
import atexit
import sys
import signal
import constants

print("Starting up...")

config = {}

if os.path.exists('/data/options.json'):
    print("Loading options.json")
    with open(r'/data/options.json') as file:
        config = json.load(file)
        safe_config = dict(config)
        for secret_key in ("mqtt_password",):
            if secret_key in safe_config:
                safe_config[secret_key] = "***"
        print("Config: " + json.dumps(safe_config))

elif os.path.exists('config.yaml'):
    print("Loading config.yaml")
    with open(r'config.yaml') as file:
        config = yaml.load(file, Loader=yaml.FullLoader)['options']
        safe_config = dict(config)
        for secret_key in ("mqtt_password",):
            if secret_key in safe_config:
                safe_config[secret_key] = "***"
        print("Config: " + json.dumps(safe_config))
        
else:
    sys.exit("No config file found")  


scan_interval = config['scan_interval']
connection_type = config.get('connection_type', 'IP')
bms_ip = str(config.get('bms_ip', '')).strip()
bms_serial = str(config.get('bms_serial', '')).strip()
try:
    bms_port = int(config.get('bms_port', 0))
except (TypeError, ValueError):
    bms_port = 0

if connection_type == "IP":
    if len(bms_ip) == 0:
        sys.exit("Configuration error: connection_type 'IP' requires bms_ip")
    if bms_port <= 0:
        sys.exit("Configuration error: connection_type 'IP' requires valid bms_port")
elif connection_type == "Serial":
    if len(bms_serial) == 0:
        sys.exit("Configuration error: connection_type 'Serial' requires bms_serial")
else:
    sys.exit("Configuration error: connection_type must be 'IP' or 'Serial'")

ha_discovery_enabled = config['mqtt_ha_discovery']
# Optional tuning knob for installations where packet layout drifts between packs.
# Default 0 keeps existing behavior.
bms_force_pack_offset = int(config.get('force_pack_offset', 0))
# Optional zero-padding for MQTT entity/topic numbering.
zero_pad_number_cells = max(0, int(config.get('zero_pad_number_cells', 0)))
zero_pad_number_packs = max(0, int(config.get('zero_pad_number_packs', 0)))
# Retry values are optional in config; defaults keep backward compatibility.
bms_connect_retries = max(1, int(config.get('bms_connect_retries', 5)))
bms_connect_retry_delay = max(1, int(config.get('bms_connect_retry_delay', 5)))
code_running = True
bms_connected = False
mqtt_connected = False
bms = None
shutdown_started = False
print_initial = True
debug_output = config['debug_output']
disc_payload = {}
repub_discovery = 0

bms_version = ''
bms_sn = ''
pack_sn = ''
packs = 1
cells = 13
temps = 6
# Persistent TCP receive buffer for partial/combined socket frames.
tcp_rx_buffer = b""


print("Connection Type: " + connection_type)


def fmt_pack(pack_number):
    if zero_pad_number_packs > 0:
        return str(pack_number).zfill(zero_pad_number_packs)
    return str(pack_number)


def fmt_cell(cell_number):
    if zero_pad_number_cells > 0:
        return str(cell_number).zfill(zero_pad_number_cells)
    return str(cell_number)

def on_connect(client, userdata, flags, rc):
    print("MQTT connected with result code "+str(rc))
    global mqtt_connected
    mqtt_connected = True

def on_disconnect(client, userdata, rc):
    print("MQTT disconnected with result code "+str(rc))
    global mqtt_connected
    mqtt_connected = False


client = mqtt.Client()
client.on_connect = on_connect
client.on_disconnect = on_disconnect
#client.on_message = on_message

# LWT must be set before connect() so broker can register it for unexpected disconnects.
client.will_set(config['mqtt_base_topic'] + "/availability","offline", qos=0, retain=False)
client.username_pw_set(username=config['mqtt_user'], password=config['mqtt_password'])

def mqtt_connect():
    # Wrapper used by startup and reconnect loop to keep connect error handling in one place.
    global mqtt_connected
    try:
        client.connect(config['mqtt_host'], config['mqtt_port'], 60)
        return True
    except socket.timeout:
        mqtt_connected = False
        print("MQTT connect timeout to " + config['mqtt_host'] + ":" + str(config['mqtt_port']))
        return False
    except OSError as e:
        mqtt_connected = False
        print("MQTT socket error connecting: %s" % e)
        return False
    except Exception as e:
        mqtt_connected = False
        print("MQTT error connecting: %s" % e)
        return False

if mqtt_connect():
    client.loop_start()
    time.sleep(2)
else:
    print("MQTT not connected on startup, will retry...")

def graceful_shutdown(reason="shutdown request"):
    global shutdown_started
    global code_running
    global bms_connected
    global mqtt_connected
    global bms

    if shutdown_started:
        return
    shutdown_started = True

    print("Shutdown requested (" + str(reason) + "), starting graceful shutdown...")
    code_running = False

    if bms_connected and bms:
        print("Disconnecting from BMS...")
        try:
            bms.close()
            print("BMS disconnected")
        except Exception as e:
            print("Error disconnecting BMS: " + str(e))
        bms_connected = False
    else:
        print("BMS already disconnected")

    print("Disconnecting MQTT...")
    try:
        client.publish(config['mqtt_base_topic'] + "/availability","offline")
    except Exception as e:
        print("Error publishing MQTT offline availability: " + str(e))

    try:
        client.loop_stop()
    except Exception as e:
        print("Error stopping MQTT loop: " + str(e))

    try:
        client.disconnect()
        print("MQTT disconnected")
    except Exception as e:
        print("Error disconnecting MQTT: " + str(e))
    mqtt_connected = False


def handle_shutdown_signal(signum, frame):
    graceful_shutdown("signal " + str(signum))
    sys.exit(0)


def exit_handler():
    graceful_shutdown("atexit")
    return

atexit.register(exit_handler)
signal.signal(signal.SIGTERM, handle_shutdown_signal)
signal.signal(signal.SIGINT, handle_shutdown_signal)

def bms_connect(address, port):

    if connection_type == "Serial":

        try:
            print("trying to connect %s" % bms_serial)
            s = serial.Serial(bms_serial,timeout = 1)
            print("BMS serial connected")
            return s, True
        except IOError as msg:
            print("BMS serial error connecting: %s" % msg)
            return False, False    

    else:

        try:
            print("trying to connect " + address + ":" + str(port))
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect((address, port))
            print("BMS socket connected")
            return s, True
        except OSError as msg:
            print("BMS socket error connecting: %s" % msg)
            return False, False

def bms_connect_with_retries(address, port, max_attempts=None, retry_delay=None):
    # BMS links can be noisy on boot; retry before giving up.
    if max_attempts is None:
        max_attempts = bms_connect_retries
    if retry_delay is None:
        retry_delay = bms_connect_retry_delay

    for attempt in range(1, max_attempts + 1):
        bms, connected = bms_connect(address, port)
        if connected:
            return bms, True
        if attempt < max_attempts:
            print("BMS connect attempt " + str(attempt) + "/" + str(max_attempts) + " failed, retrying in " + str(retry_delay) + "s...")
            time.sleep(retry_delay)

    print("BMS connect failed after " + str(max_attempts) + " attempts")
    return False, False

def bms_sendData(comms,request=''):

    if connection_type == "Serial":

        try:
            if len(request) > 0:
                comms.write(request)
                time.sleep(0.25)
                return True
        except IOError as e:
            print("BMS serial error: %s" % e)
            # global bms_connected
            return False

    else:

        try:
            if len(request) > 0:
                comms.send(request)
                time.sleep(0.25)
                return True
        except Exception as e:
            print("BMS socket error: %s" % e)
            # global bms_connected
            return False

def bms_get_data(comms):
    global tcp_rx_buffer
    try:
        if connection_type == "Serial":
            inc_data = comms.readline()
        else:
            # Accumulate bytes until a full protocol frame (~ ... \r) is available.
            deadline = time.monotonic() + 5
            max_buffer_size = 16384

            while time.monotonic() < deadline:
                soi_index = tcp_rx_buffer.find(b"\x7e")
                if soi_index >= 0:
                    eoi_index = tcp_rx_buffer.find(b"\x0d", soi_index + 1)
                    if eoi_index >= 0:
                        # Drop any noise before SOI so parsing stays aligned.
                        if (soi_index > 0) and (debug_output > 0):
                            dropped = tcp_rx_buffer[:soi_index]
                            print("Discarding preamble bytes: " + str(dropped) + " |Hex: " + str(dropped.hex(" ")))
                        inc_data = tcp_rx_buffer[soi_index:eoi_index + 1]
                        tcp_rx_buffer = tcp_rx_buffer[eoi_index + 1:]
                        return inc_data
                elif len(tcp_rx_buffer) > max_buffer_size:
                    if debug_output > 0:
                        print("RX buffer overflow without SOI; clearing stale data")
                    tcp_rx_buffer = b""

                try:
                    temp = comms.recv(4096)
                    if len(temp) == 0:
                        raise ConnectionError("Socket closed by remote host")
                    tcp_rx_buffer += temp
                except socket.timeout:
                    # Keep waiting until deadline; higher-level logic decides on retries.
                    continue

                if len(tcp_rx_buffer) > max_buffer_size:
                    if debug_output > 0:
                        print("RX buffer exceeded max size; trimming stale data")
                    keep_from = tcp_rx_buffer.rfind(b"\x7e")
                    if keep_from >= 0:
                        tcp_rx_buffer = tcp_rx_buffer[keep_from:]
                    else:
                        tcp_rx_buffer = b""

            print("BMS socket receive timeout waiting for full frame")
            return False
        return inc_data
    except Exception as e:
        print("BMS socket receive error: %s" % e)
        # global bms_connected
        return False

def ha_discovery():

    global ha_discovery_enabled
    global packs
    fmt_pack = globals().get("fmt_pack", lambda n: str(n))
    fmt_cell = globals().get("fmt_cell", lambda n: str(n))

    if ha_discovery_enabled:
        
        print("Publishing HA Discovery topic...")

        def publish_discovery(topic, payload, qos=0, retain=True):
            # In debug mode, print each discovery entity so users can trace what was published to MQTT.
            if debug_output >= 1:
                entity_name = disc_payload.get('name', 'unknown')
                state_topic = disc_payload.get('state_topic', 'unknown')
                print("HA discovery publish: " + entity_name + " -> " + state_topic)
            client.publish(topic, payload, qos=qos, retain=retain)

        disc_payload['availability_topic'] = config['mqtt_base_topic'] + "/availability"

        device = {}
        device['manufacturer'] = "BMS Pace"
        device['model'] = "AM-x"
        device['identifiers'] = "bmspace_" + bms_sn
        device['name'] = "Generic Lithium"
        device['sw_version'] = bms_version
        disc_payload['device'] = device

        for p in range (1,packs+1):

            for i in range(0,cells):
                disc_payload['name'] = "Pack " + fmt_pack(p) + " Cell " + fmt_cell(i+1) + " Voltage"
                disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_v_cell_" + fmt_cell(i+1)
                disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/v_cells/cell_" + fmt_cell(i+1)
                disc_payload['unit_of_measurement'] = "mV"
                publish_discovery(config['mqtt_ha_discovery_topic']+"/sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            for i in range(0,temps):
                disc_payload['name'] = "Pack " + fmt_pack(p) + " Temperature " + str(i+1)
                disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_temp_" + str(i+1)
                disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/temps/temp_" + str(i+1)
                disc_payload['unit_of_measurement'] = "°C"
                publish_discovery(config['mqtt_ha_discovery_topic']+"/sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            # disc_payload['name'] = "MOS_Temp"
            # disc_payload['unique_id'] = "bmspace_" + bms_sn + "_t_mos"
            # disc_payload['state_topic'] = config['mqtt_base_topic'] + "/t_mos"
            # disc_payload['unit_of_measurement'] = "°C"
            # publish_discovery(config['mqtt_ha_discovery_topic']+"/sensor/BMS-" + bms_sn + "/" + disc_payload['name'] + "/config",json.dumps(disc_payload),qos=0, retain=True)

            # disc_payload['name'] = "Environmental_Temp"
            # disc_payload['unique_id'] = "bmspace_" + bms_sn + "_t_env"
            # disc_payload['state_topic'] = config['mqtt_base_topic'] + "/t_env"
            # disc_payload['unit_of_measurement'] = "°C"
            # publish_discovery(config['mqtt_ha_discovery_topic']+"/sensor/BMS-" + bms_sn + "/" + disc_payload['name'] + "/config",json.dumps(disc_payload),qos=0, retain=True)

            disc_payload['name'] = "Pack " + fmt_pack(p) + " Current"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_i_pack"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/i_pack"
            disc_payload['unit_of_measurement'] = "A"
            publish_discovery(config['mqtt_ha_discovery_topic']+"/sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            disc_payload['name'] = "Pack " + fmt_pack(p) + " Voltage"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_v_pack"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/v_pack"
            disc_payload['unit_of_measurement'] = "V"
            publish_discovery(config['mqtt_ha_discovery_topic']+"/sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            disc_payload['name'] = "Pack " + fmt_pack(p) + " Remaining Capacity"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_i_remain_cap"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/i_remain_cap"
            disc_payload['unit_of_measurement'] = "mAh"
            publish_discovery(config['mqtt_ha_discovery_topic']+"/sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            disc_payload['name'] = "Pack " + fmt_pack(p) + " State of Health"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_soh"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/soh"
            disc_payload['unit_of_measurement'] = "%"
            publish_discovery(config['mqtt_ha_discovery_topic']+"/sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            disc_payload['name'] = "Pack " + fmt_pack(p) + " Cycles"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_cycles"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/cycles"
            disc_payload['unit_of_measurement'] = ""
            publish_discovery(config['mqtt_ha_discovery_topic']+"/sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            disc_payload['name'] = "Pack " + fmt_pack(p) + " Full Capacity"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_i_full_cap"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/i_full_cap"
            disc_payload['unit_of_measurement'] = "mAh"
            publish_discovery(config['mqtt_ha_discovery_topic']+"/sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            disc_payload['name'] = "Pack " + fmt_pack(p) + " Design Capacity"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_i_design_cap"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/i_design_cap"
            disc_payload['unit_of_measurement'] = "mAh"
            publish_discovery(config['mqtt_ha_discovery_topic']+"/sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            disc_payload['name'] = "Pack " + fmt_pack(p) + " State of Charge"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_soc"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/soc"
            disc_payload['unit_of_measurement'] = "%"
            publish_discovery(config['mqtt_ha_discovery_topic']+"/sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            # Remove optional unit field when switching to non-numeric entities.
            disc_payload.pop('unit_of_measurement', None)

            disc_payload['name'] = "Pack " + fmt_pack(p) + " Warnings"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_warnings"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/warnings"
            publish_discovery(config['mqtt_ha_discovery_topic']+"/sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            disc_payload['name'] = "Pack " + fmt_pack(p) + " Balancing1"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_balancing1"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/balancing1"
            publish_discovery(config['mqtt_ha_discovery_topic']+"/sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            disc_payload['name'] = "Pack " + fmt_pack(p) + " Balancing2"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_balancing2"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/balancing2"
            publish_discovery(config['mqtt_ha_discovery_topic']+"/sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)


            # Binary Sensors
            disc_payload['name'] = "Pack " + fmt_pack(p) + " Protection Short Circuit"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_prot_short_circuit"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/prot_short_circuit"
            disc_payload['payload_on'] = "1"
            disc_payload['payload_off'] = "0"
            publish_discovery(config['mqtt_ha_discovery_topic']+"/binary_sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            disc_payload['name'] = "Pack " + fmt_pack(p) + " Protection Discharge Current"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_prot_discharge_current"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/prot_discharge_current"
            disc_payload['payload_on'] = "1"
            disc_payload['payload_off'] = "0"
            publish_discovery(config['mqtt_ha_discovery_topic']+"/binary_sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            disc_payload['name'] = "Pack " + fmt_pack(p) + " Protection Charge Current"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_prot_charge_current"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/prot_charge_current"
            disc_payload['payload_on'] = "1"
            disc_payload['payload_off'] = "0"
            publish_discovery(config['mqtt_ha_discovery_topic']+"/binary_sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            disc_payload['name'] = "Pack " + fmt_pack(p) + " Current Limit"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_current_limit"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/current_limit"
            disc_payload['payload_on'] = "1"
            disc_payload['payload_off'] = "0"
            publish_discovery(config['mqtt_ha_discovery_topic']+"/binary_sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            disc_payload['name'] = "Pack " + fmt_pack(p) + " Charge FET"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_charge_fet"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/charge_fet"
            disc_payload['payload_on'] = "1"
            disc_payload['payload_off'] = "0"
            publish_discovery(config['mqtt_ha_discovery_topic']+"/binary_sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            disc_payload['name'] = "Pack " + fmt_pack(p) + " Discharge FET"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_discharge_fet"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/discharge_fet"
            disc_payload['payload_on'] = "1"
            disc_payload['payload_off'] = "0"
            publish_discovery(config['mqtt_ha_discovery_topic']+"/binary_sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            disc_payload['name'] = "Pack " + fmt_pack(p) + " Pack Indicate"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_pack_indicate"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/pack_indicate"
            disc_payload['payload_on'] = "1"
            disc_payload['payload_off'] = "0"
            publish_discovery(config['mqtt_ha_discovery_topic']+"/binary_sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            disc_payload['name'] = "Pack " + fmt_pack(p) + " Reverse"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_reverse"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/reverse"
            disc_payload['payload_on'] = "1"
            disc_payload['payload_off'] = "0"
            publish_discovery(config['mqtt_ha_discovery_topic']+"/binary_sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            disc_payload['name'] = "Pack " + fmt_pack(p) + " AC In"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_ac_in"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/ac_in"
            disc_payload['payload_on'] = "1"
            disc_payload['payload_off'] = "0"
            publish_discovery(config['mqtt_ha_discovery_topic']+"/binary_sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            disc_payload['name'] = "Pack " + fmt_pack(p) + " Heart"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_heart"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/heart"
            disc_payload['payload_on'] = "1"
            disc_payload['payload_off'] = "0"
            publish_discovery(config['mqtt_ha_discovery_topic']+"/binary_sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

            disc_payload['name'] = "Pack " + fmt_pack(p) + " Cell Max Volt Diff"
            disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_" + fmt_pack(p) + "_cells_max_diff_calc"
            disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/cells_max_diff_calc"
            disc_payload['unit_of_measurement'] = "mV"
            publish_discovery(config['mqtt_ha_discovery_topic']+"/sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

        # Publish aggregate pack entities once (not once per pack).
        disc_payload.pop('payload_on', None)
        disc_payload.pop('payload_off', None)
        disc_payload.pop('unit_of_measurement', None)

        disc_payload['name'] = "Pack Remaining Capacity"
        disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_i_remain_cap"
        disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_remain_cap"
        disc_payload['unit_of_measurement'] = "mAh"
        publish_discovery(config['mqtt_ha_discovery_topic']+"/sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

        disc_payload['name'] = "Pack Full Capacity"
        disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_i_full_cap"
        disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_full_cap"
        disc_payload['unit_of_measurement'] = "mAh"
        publish_discovery(config['mqtt_ha_discovery_topic']+"/sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

        disc_payload['name'] = "Pack Design Capacity"
        disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_i_design_cap"
        disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_design_cap"
        disc_payload['unit_of_measurement'] = "mAh"
        publish_discovery(config['mqtt_ha_discovery_topic']+"/sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

        disc_payload['name'] = "Pack State of Charge"
        disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_soc"
        disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_soc"
        disc_payload['unit_of_measurement'] = "%"
        publish_discovery(config['mqtt_ha_discovery_topic']+"/sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

        disc_payload['name'] = "Pack State of Health"
        disc_payload['unique_id'] = "bmspace_" + bms_sn + "_pack_soh"
        disc_payload['state_topic'] = config['mqtt_base_topic'] + "/pack_soh"
        disc_payload['unit_of_measurement'] = "%"
        publish_discovery(config['mqtt_ha_discovery_topic']+"/sensor/BMS-" + bms_sn + "/" + disc_payload['name'].replace(' ', '_') + "/config",json.dumps(disc_payload),qos=0, retain=True)

        print("Finished - Publishing HA Discovery topic")

    else:
        print("HA Discovery Disabled")

def chksum_calc(data):

    global debug_output
    chksum = 0

    try:

        for element in range(1, len(data)): #-5):
            chksum += (data[element])
        
        chksum = chksum % 65536
        chksum = '{0:016b}'.format(chksum)
    
        flip_bits = '' 
        for i in chksum:
            if i == '0':
                flip_bits += '1'
            else:
                flip_bits += '0'

        chksum = flip_bits
        chksum = int(chksum,2)+1

        chksum = format(chksum, 'X')

    except Exception as e:
        if debug_output > 0:
            print("Error calculating CHKSUM using data: " + data)
            print("Error details: ", str(e))
        return(False)

    return(chksum)

def cid2_rtn(rtn):

    # RTN Reponse codes, looking for errors
    if rtn == b'00':
        return False, False
    elif rtn == b'01':
        return True, "RTN Error 01: Undefined RTN error"
    elif rtn == b'02':
        return True, "RTN Error 02: CHKSUM error"
    elif rtn == b'03':
        return True, "RTN Error 03: LCHKSUM error"
    elif rtn == b'04':
        return True, "RTN Error 04: CID2 undefined"
    elif rtn == b'05':
        return True, "RTN Error 05: Undefined error"
    elif rtn == b'06':
        return True, "RTN Error 06: Undefined error"
    elif rtn == b'09':
        return True, "RTN Error 09: Operation or write error"
    else:
        return False, False

def bms_parse_data(inc_data):

    global debug_output

    #inc_data = b'~2501460070DC00020D100A0FF40FEA100E10040FF50FFD10010FFD0FE50FF5100A1001060BD20BD20BD40BD40BF80C1AFF7ECFCF258B02271001372710600D0FFA0FFC0FFB0FFC0FFE0FFB0FFA0FFA0FFB0FFD0FFC0FFB0FFB060BEF0BF10BEF0BED0BF70C04FDB1D02E29A9022AF80\r'
    
    try:
        
        SOI = hex(ord(inc_data[0:1]))
        if SOI != '0x7e':
            return(False,"Incorrect starting byte for incoming data")

        if debug_output > 1:
            print("SOI: ", SOI)
            print("VER: ", inc_data[1:3])
            print("ADR: ", inc_data[3:5])
            print("CID1 (Type): ", inc_data[5:7])

        RTN = inc_data[7:9]
        error, info = cid2_rtn(RTN)
        if error:
            print(info)
            raise Exception(info)
        
        LCHKSUM = inc_data[9]

        if debug_output > 1:
            print("RTN: ", RTN)
            print("LENGTH: ", inc_data[9:13])
            print(" - LCHKSUM: ", LCHKSUM)
            print(" - LENID: ", inc_data[10:13])

        LENID = int(inc_data[10:13],16) #amount of bytes, i.e. 2x hex

        calc_LCHKSUM = lchksum_calc(inc_data[10:13])
        if calc_LCHKSUM == False:
            return(False,"Error calculating LCHKSUM for incoming data")

        if LCHKSUM != ord(calc_LCHKSUM):
            if debug_output > 0:
                print("LCHKSUM received: " + str(LCHKSUM) + " does not match calculated: " + str(ord(calc_LCHKSUM)))
            return(False,"LCHKSUM received: " + str(LCHKSUM) + " does not match calculated: " + str(ord(calc_LCHKSUM)))

        if debug_output > 1:
            print(" - LENID (int): ", LENID)

        INFO = inc_data[13:13+LENID]

        if debug_output > 1:
            print("INFO: ", INFO)

        CHKSUM = inc_data[13+LENID:13+LENID+4]

        if debug_output > 1:
            print("CHKSUM: ", CHKSUM)
            #print("EOI: ", hex(inc_data[13+LENID+4]))

        calc_CHKSUM = chksum_calc(inc_data[:len(inc_data)-5])


        if debug_output > 1:
            print("Calc CHKSUM: ", calc_CHKSUM)
    except Exception as e:
        if debug_output > 0:
            print("Error1 calculating CHKSUM using data: ", inc_data)
        return(False,"Error1 calculating CHKSUM: " + str(e))

    if calc_CHKSUM == False:
        if debug_output > 0:
            print("Error2 calculating CHKSUM using data: ", inc_data)
        return(False,"Error2 calculating CHKSUM")

    if CHKSUM.decode("ASCII") == calc_CHKSUM:
        return(True,INFO)
    else:
        if debug_output > 0:
            print("Received and calculated CHKSUM does not match: Received: " + CHKSUM.decode("ASCII") + ", Calculated: " + calc_CHKSUM)
            print("...for incoming data: " + str(inc_data) + " |Hex: " + str(inc_data.hex(' ')))
            print("Length of incoming data as measured: " + str(len(inc_data)))
            print("SOI: ", SOI)
            print("VER: ", inc_data[1:3])
            print("ADR: ", inc_data[3:5])
            print("CID1 (Type): ", inc_data[5:7])
            print("RTN (decode!): ", RTN)
            print("LENGTH: ", inc_data[9:13])
            print(" - LCHKSUM: ", inc_data[9])
            print(" - LENID: ", inc_data[10:13])
            print(" - LENID (int): ", int(inc_data[10:13],16))
            print("INFO: ", INFO)
            print("CHKSUM: ", CHKSUM)
            #print("EOI: ", hex(inc_data[13+LENID+4]))
        return(False,"Checksum error")

def lchksum_calc(lenid):

    chksum = 0

    try:

        # for element in range(1, len(lenid)): #-5):
        #     chksum += (lenid[element])
        
        for element in range(0, len(lenid)):
            chksum += int(chr(lenid[element]),16)

        chksum = chksum % 16
        chksum = '{0:04b}'.format(chksum)

        flip_bits = '' 
        for i in chksum:
            if i == '0':
                flip_bits += '1'
            else:
                flip_bits += '0'

        chksum = flip_bits
        chksum = int(chksum,2)

        chksum += 1

        if chksum > 15:
            chksum = 0

        chksum = format(chksum, 'X')

    except:

        print("Error calculating LCHKSUM using LENID: ", lenid)
        return(False)

    return(chksum)

def bms_request(bms, ver=b"\x32\x35",adr=b"\x30\x31",cid1=b"\x34\x36",cid2=b"\x43\x31",info=b"",LENID=False):

    global bms_connected
    global debug_output
    
    request = b'\x7e'
    request += ver
    request += adr
    request += cid1
    request += cid2

    if not(LENID):
        LENID = len(info)
        #print("Length: ", LENID)
        LENID = bytes(format(LENID, '03X'), "ASCII")

    #print("LENID: ", LENID)

    if LENID == b'000':
        LCHKSUM = '0'
    else:
        LCHKSUM = lchksum_calc(LENID)
        if LCHKSUM == False:
            return(False,"Error calculating LCHKSUM)")
    #print("LCHKSUM: ", LCHKSUM)
    request += bytes(LCHKSUM, "ASCII")
    request += LENID
    request += info
    CHKSUM = bytes(chksum_calc(request), "ASCII")
    if CHKSUM == False:
        return(False,"Error calculating CHKSUM)")
    request += CHKSUM
    request += b'\x0d'

    if debug_output > 2:
        print("-> Outgoing Data: ", request)

    if not bms_sendData(bms,request):
        bms_connected = False
        print("Error, connection to BMS lost")
        return(False,"Error, connection to BMS lost")

    inc_data = bms_get_data(bms)

    if inc_data == False:
        print("Error retrieving data from BMS")
        return(False,"Error retrieving data from BMS")

    if debug_output > 2:
        print("<- Incoming data: ", inc_data)

    success, INFO = bms_parse_data(inc_data)

    return(success, INFO)

def bms_getPackNumber(bms):
    # Some PACE stacks only report local pack for CID2=0x90 unless broadcast ADR is used.
    # Try broadcast/system addresses first, then fall back to analog-all data parsing.
    for adr in (b"FF", b"00", b"01"):
        success, INFO = bms_request(bms,adr=adr,cid2=constants.cid2PackNumber)
        if success != False:
            try:
                packNumber = int(INFO,16)
                if debug_output > 0:
                    print("Total battery packs reported via CID2=0x90 ADR " + adr.decode("ascii") + ": " + str(packNumber))
                # Return immediately when a multi-pack count is reported.
                # If not, keep probing/fallback because some units only return local-pack style values here.
                if packNumber > 1:
                    return(True,packNumber)
            except Exception:
                if debug_output > 0:
                    print("Error extracting total battery count in pack for ADR " + adr.decode("ascii"))

    # Fallback: CID2=0x42 (analog) with INFO=FF includes total pack count in payload.
    success, inc_data = bms_request(bms,adr=b"FF",cid2=constants.cid2PackAnalogData,info=b"FF")
    if success == False:
        return(False,inc_data)

    try:
        packNumber = int(inc_data[2:4],16)
        if debug_output > 0:
            print("Total battery packs reported via CID2=0x42 fallback: " + str(packNumber))
        return(True,packNumber)
    except Exception:
        print("Error extracting total battery count from analog fallback response")
        return(False,"Error extracting total battery count from analog fallback response")

def bms_getVersion(comms, reported_packs=1):

    global bms_version

    success, INFO = bms_request(comms,cid2=constants.cid2SoftwareVersion)

    if success == False:
        return(False,INFO)

    try:

        bms_version = bytes.fromhex(INFO.decode("ascii")).decode("ASCII")
        client.publish(config['mqtt_base_topic'] + "/bms_version",bms_version)
        print("BMS Version: " + bms_version)
    except:
        return(False,"Error extracting BMS version")

    return(success,bms_version)

def bms_getSerial(comms, reported_packs=1):

    global bms_sn
    global pack_sn

    success, INFO = bms_request(comms,cid2=constants.cid2SerialNumber)

    if success == False:
        print("Error: " + INFO)
        return(False,INFO, False)

    try:

        bms_sn = bytes.fromhex(INFO[0:30].decode("ascii")).decode("ASCII").replace(" ", "")
        pack_sn = bytes.fromhex(INFO[40:68].decode("ascii")).decode("ASCII").replace(" ", "")
        client.publish(config['mqtt_base_topic'] + "/bms_sn",bms_sn)
        client.publish(config['mqtt_base_topic'] + "/pack_sn",pack_sn)
        print("BMS Serial Number: " + bms_sn)
        print("Pack Serial Number: " + pack_sn)

    except:
        return(False,"Error extracting BMS version", False)

    return(success,bms_sn,pack_sn)

def bms_getAnalogData(bms,batNumber):

    global print_initial
    global cells
    global temps
    global packs
    fmt_pack = globals().get("fmt_pack", lambda n: str(n))
    fmt_cell = globals().get("fmt_cell", lambda n: str(n))
    byte_index = 2
    i_pack = []
    v_pack = []
    i_remain_cap = []
    i_design_cap = []
    cycles = []
    i_full_cap = []
    soc = []
    soh = []

    battery = bytes(format(batNumber, '02X'), 'ASCII')
    # print("Get analog info for battery: ", battery)

    success, inc_data = bms_request(bms,cid2=constants.cid2PackAnalogData,info=battery)

    if success == False:
        return(False,inc_data)

    try:
        def read_hex(length, field_name):
            nonlocal byte_index
            if byte_index + length > len(inc_data):
                raise ValueError(
                    "Truncated analog payload at " + field_name +
                    " (index " + str(byte_index) + ", need " + str(length) + " hex chars, total " + str(len(inc_data)) + ")"
                )
            raw = inc_data[byte_index:byte_index+length]
            byte_index += length
            return int(raw,16)

        packs = read_hex(2, "packs")
        if print_initial:
            print("Packs: " + str(packs))

        v_cell = {}
        t_cell = {}

        for p in range(1,packs+1):
            # Each pack block starts with its cell-count marker.
            # We read this marker first, then parse that many cell voltages.
            cells = read_hex(2, "pack " + fmt_pack(p) + " cell count")

            if print_initial:
                print("Pack " + fmt_pack(p) + ", Total cells: " + str(cells))
            
            cell_min_volt = 0
            cell_max_volt = 0

            for i in range(0,cells):
                v_cell[(p-1,i)] = read_hex(4, "pack " + fmt_pack(p) + " cell " + fmt_cell(i+1) + " voltage")
                client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/v_cells/cell_" + fmt_cell(i+1) ,str(v_cell[(p-1,i)]))
                if print_initial:
                    print("Pack " + fmt_pack(p) +", V Cell" + fmt_cell(i+1) + ": " + str(v_cell[(p-1,i)]) + " mV")

                #Calculate cell max and min volt
                if i == 0:
                    cell_min_volt = v_cell[(p-1,i)]
                    cell_max_volt = v_cell[(p-1,i)]
                else:
                    if v_cell[(p-1,i)] < cell_min_volt:
                        cell_min_volt = v_cell[(p-1,i)]
                    if v_cell[(p-1,i)] > cell_max_volt:
                        cell_max_volt = v_cell[(p-1,i)]
           
            #Calculate cells max diff volt
            cell_max_diff_volt = cell_max_volt - cell_min_volt
            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/cells_max_diff_calc" ,str(cell_max_diff_volt))
            if print_initial:
                print("Pack " + fmt_pack(p) +", Cell Max Diff Volt Calc: " + str(cell_max_diff_volt) + " mV")

            temps = read_hex(2, "pack " + fmt_pack(p) + " temperature count")
            if print_initial:
                print("Pack " + fmt_pack(p) + ", Total temperature sensors: " + str(temps))

            for i in range(0,temps): #temps-2
                t_cell[(p-1,i)] = (read_hex(4, "pack " + fmt_pack(p) + " temp " + str(i+1)) - 2730)/10
                client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/temps/temp_" + str(i+1) ,str(round(t_cell[(p-1,i)],1)))
                if print_initial:
                    print("Pack " + fmt_pack(p) + ", Temp" + str(i+1) + ": " + str(round(t_cell[(p-1,i)],1)) + " ℃")

            # t_mos= (int(inc_data[byte_index:byte_index+4],16))/160-273
            # client.publish(config['mqtt_base_topic'] + "/t_mos",str(round(t_mos,1)))
            # if print_initial:
            #     print("T Mos: " + str(t_mos) + " Deg")

            # t_env= (int(inc_data[byte_index:byte_index+4],16))/160-273
            # client.publish(config['mqtt_base_topic'] + "/t_env",str(round(t_env,1)))
            # offset += 7
            # if print_initial:
            #     print("T Env: " + str(t_env) + " Deg")

            i_pack.append(read_hex(4, "pack " + fmt_pack(p) + " current"))
            if i_pack[p-1] >= 32768:
                i_pack[p-1] -= 65536
            i_pack[p-1] = i_pack[p-1]/100
            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/i_pack",str(i_pack[p-1]))
            if print_initial:
                print("Pack " + fmt_pack(p) + ", I Pack: " + str(i_pack[p-1]) + " A")

            v_pack.append(read_hex(4, "pack " + fmt_pack(p) + " voltage")/1000)
            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/v_pack",str(v_pack[p-1]))
            if print_initial:
                print("Pack " + fmt_pack(p) + ", V Pack: " + str(v_pack[p-1]) + " V")

            i_remain_cap.append(read_hex(4, "pack " + fmt_pack(p) + " remaining capacity")*10)
            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/i_remain_cap",str(i_remain_cap[p-1]))
            if print_initial:
                print("Pack " + fmt_pack(p) + ", I Remaining Capacity: " + str(i_remain_cap[p-1]) + " mAh")

            if byte_index + 2 > len(inc_data):
                raise ValueError("Truncated analog payload before pack " + fmt_pack(p) + " reserved field")
            byte_index += 2 # Manual: Define number P = 3

            i_full_cap.append(read_hex(4, "pack " + fmt_pack(p) + " full capacity")*10)
            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/i_full_cap",str(i_full_cap[p-1]))
            if print_initial:
                print("Pack " + fmt_pack(p) + ", I Full Capacity: " + str(i_full_cap[p-1]) + " mAh")

            if i_full_cap[p-1] > 0:
                soc.append(round(i_remain_cap[p-1]/i_full_cap[p-1]*100,2))
            else:
                if debug_output > 0:
                    print("Pack " + fmt_pack(p) + ": i_full_cap is 0, forcing SOC to 0")
                soc.append(0)
            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/soc",str(soc[p-1]))
            if print_initial:
                print("Pack " + fmt_pack(p) + ", SOC: " + str(soc[p-1]) + " %")

            cycles.append(read_hex(4, "pack " + fmt_pack(p) + " cycles"))
            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/cycles",str(cycles[p-1]))
            if print_initial:
                print("Pack " + fmt_pack(p) + ", Cycles: " + str(cycles[p-1]))

            i_design_cap.append(read_hex(4, "pack " + fmt_pack(p) + " design capacity")*10)
            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/i_design_cap",str(i_design_cap[p-1]))
            if print_initial:
                print("Pack " + fmt_pack(p) + ", Design Capacity: " + str(i_design_cap[p-1]) + " mAh")

            if i_design_cap[p-1] > 0:
                soh.append(round(i_full_cap[p-1]/i_design_cap[p-1]*100,2))
            else:
                if debug_output > 0:
                    print("Pack " + fmt_pack(p) + ": i_design_cap is 0, forcing SOH to 0")
                soh.append(0)
            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/soh",str(soh[p-1]))
            if print_initial:
                print("Pack " + fmt_pack(p) + ", SOH: " + str(soh[p-1]) + " %")

            # Some firmware variants omit this trailing word on the last pack.
            if byte_index + 2 <= len(inc_data):
                byte_index += 2

            # Some BMS payloads include extra bytes between pack blocks.
            # This offset allows manual correction without changing parser logic.
            byte_index += bms_force_pack_offset

            # Before parsing the next pack, realign byte_index to the next pack marker.
            # We use the expected cell-count marker as an anchor and scan in 2-byte steps
            # to skip INFOFLAG/extra words that may appear between packs.
            if p < packs:
                aligned = False
                while byte_index + 2 <= len(inc_data):
                    if cells == int(inc_data[byte_index:byte_index+2],16):
                        aligned = True
                        break
                    byte_index += 2
                if not aligned:
                    print("Error parsing BMS analog data: Cannot read multiple packs")
                    return(False,"Error parsing BMS analog data: Cannot read multiple packs")

    except Exception as e:
        print("Error parsing BMS analog data: ", str(e))
        return(False,"Error parsing BMS analog data: " + str(e))

    if print_initial:
        print("Script running....")

    return True,True

def bms_getPackCapacity(bms):

    byte_index = 0

    success, inc_data = bms_request(bms,cid2=constants.cid2PackCapacity) # Seem to always reply with pack 1 data, even with ADR= 0 or FF and INFO= '' or FF

    if success == False:
        return(False,inc_data)

    try:

        pack_remain_cap = int(inc_data[byte_index:byte_index+4],16)*10
        byte_index += 4
        client.publish(config['mqtt_base_topic'] + "/pack_remain_cap",str(pack_remain_cap))
        if print_initial:
            print("Pack Remaining Capacity: " + str(pack_remain_cap) + " mAh")

        pack_full_cap = int(inc_data[byte_index:byte_index+4],16)*10
        byte_index += 4
        client.publish(config['mqtt_base_topic'] + "/pack_full_cap",str(pack_full_cap))
        if print_initial:
            print("Pack Full Capacity: " + str(pack_full_cap) + " mAh")

        pack_design_cap = int(inc_data[byte_index:byte_index+4],16)*10
        byte_index += 4
        client.publish(config['mqtt_base_topic'] + "/pack_design_cap",str(pack_design_cap))
        if print_initial:
            print("Pack Design Capacity: " + str(pack_design_cap) + " mAh")

        if pack_full_cap > 0:
            pack_soc = round(pack_remain_cap/pack_full_cap*100,2)
        else:
            if debug_output > 0:
                print("Pack full capacity is 0, forcing pack SOC to 0")
            pack_soc = 0
        client.publish(config['mqtt_base_topic'] + "/pack_soc",str(pack_soc))
        if print_initial:
            print("Pack SOC: " + str(pack_soc) + " %")

        if pack_design_cap > 0:
            pack_soh = round(pack_full_cap/pack_design_cap*100,2)
        else:
            if debug_output > 0:
                print("Pack design capacity is 0, forcing pack SOH to 0")
            pack_soh = 0
        client.publish(config['mqtt_base_topic'] + "/pack_soh",str(pack_soh))
        if print_initial:
            print("Pack SOH: " + str(pack_soh) + " %")

    except Exception as e:
        print("Error parsing BMS pack capacity data: ", str(e))
        return False, "Error parsing BMS pack capacity data: " + str(e)

    return True,True

def bms_getWarnInfo(bms):

    byte_index = 2
    packsW = 1
    warnings = ""
    fmt_pack = globals().get("fmt_pack", lambda n: str(n))

    success, inc_data = bms_request(bms,cid2=constants.cid2WarnInfo,info=b'FF')

    if success == False:
        return(False,inc_data)

    #inc_data = b'000210000000000000000000000000000000000600000000000000000000000E0000000000001110000000000000000000000000000000000600000000000000000000000E00000000000000'

    try:
        def read_token(length, field_name):
            nonlocal byte_index
            if byte_index + length > len(inc_data):
                raise ValueError(
                    "Truncated warning payload at " + field_name +
                    " (index " + str(byte_index) + ", need " + str(length) + " hex chars, total " + str(len(inc_data)) + ")"
                )
            token = inc_data[byte_index:byte_index+length]
            byte_index += length
            return token

        def decode_warn(code):
            return constants.warningStates.get(code, "state 0x" + code.decode("ascii"))

        packsW = int(read_token(2, "packs"),16)
        if print_initial:
            print("Packs for warnings: " + str(packsW))

        for p in range(1,packsW+1):

            cellsW = int(read_token(2, "pack " + fmt_pack(p) + " cell count"),16)

            for c in range(1,cellsW+1):

                warn_code = read_token(2, "pack " + fmt_pack(p) + " cell " + str(c) + " warn")
                if warn_code != b'00':
                    warn = decode_warn(warn_code)
                    warnings += "cell " + str(c) + " " + warn + ", "

            tempsW = int(read_token(2, "pack " + fmt_pack(p) + " temp count"),16)
        
            for t in range(1,tempsW+1):

                warn_code = read_token(2, "pack " + fmt_pack(p) + " temp " + str(t) + " warn")
                if warn_code != b'00':
                    warn = decode_warn(warn_code)
                    warnings += "temp " + str(t) + " " + warn + ", "

            warn_code = read_token(2, "pack " + fmt_pack(p) + " charge current warn")
            if warn_code != b'00':
                warn = decode_warn(warn_code)
                warnings += "charge current " + warn + ", "

            warn_code = read_token(2, "pack " + fmt_pack(p) + " total voltage warn")
            if warn_code != b'00':
                warn = decode_warn(warn_code)
                warnings += "total voltage " + warn + ", "

            warn_code = read_token(2, "pack " + fmt_pack(p) + " discharge current warn")
            if warn_code != b'00':
                warn = decode_warn(warn_code)
                warnings += "discharge current " + warn + ", "

            protectState1 = int(read_token(2, "pack " + fmt_pack(p) + " protectState1"),16)
            if protectState1 > 0:
                warnings += "Protection State 1: "
                for x in range(0,8):
                    if (protectState1 & (1<<x)):
                        warnings += constants.protectState1[x+1] + " | "
                warnings = warnings.rstrip("| ")
                warnings += ", "
            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/prot_short_circuit",str(protectState1>>6 & 1))  
            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/prot_discharge_current",str(protectState1>>5 & 1))  
            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/prot_charge_current",str(protectState1>>4 & 1))  

            protectState2 = int(read_token(2, "pack " + fmt_pack(p) + " protectState2"),16)
            if protectState2 > 0:
                warnings += "Protection State 2: "
                for x in range(0,8):
                    if (protectState2 & (1<<x)):
                        warnings += constants.protectState2[x+1] + " | "
                warnings = warnings.rstrip("| ")
                warnings += ", "
            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/fully",str(protectState2>>7 & 1))  

            # instructionState = ord(bytes.fromhex(inc_data[byte_index:byte_index+2].decode('ascii')))
            # if instructionState > 0:
            #     warnings += "Instruction State: "
            #     for x in range(0,8):
            #         if (instructionState & (1<<x)):
            #              warnings += constants.instructionState[x+1] + " | "
            #     warnings = warnings.rstrip("| ")
            #     warnings += ", "  
            # byte_index += 2

            instructionState = int(read_token(2, "pack " + fmt_pack(p) + " instructionState"),16)
            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/current_limit",str(instructionState>>0 & 1))
            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/charge_fet",str(instructionState>>1 & 1))
            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/discharge_fet",str(instructionState>>2 & 1))
            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/pack_indicate",str(instructionState>>3 & 1))
            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/reverse",str(instructionState>>4 & 1))
            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/ac_in",str(instructionState>>5 & 1))
            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/heart",str(instructionState>>7 & 1))

            controlState = int(read_token(2, "pack " + fmt_pack(p) + " controlState"),16)
            if controlState > 0:
                warnings += "Control State: "
                for x in range(0,8):
                    if (controlState & (1<<x)):
                        warnings += constants.controlState[x+1] + " | "
                warnings = warnings.rstrip("| ")
                warnings += ", "  

            faultState = int(read_token(2, "pack " + fmt_pack(p) + " faultState"),16)
            if faultState > 0:
                warnings += "Fault State: "
                for x in range(0,8):
                    if (faultState & (1<<x)):
                        warnings += constants.faultState[x+1] + " | "
                warnings = warnings.rstrip("| ")
                warnings += ", "  

            balanceState1 = '{0:08b}'.format(int(read_token(2, "pack " + fmt_pack(p) + " balancing1"),16))

            balanceState2 = '{0:08b}'.format(int(read_token(2, "pack " + fmt_pack(p) + " balancing2"),16))

            warnState1 = int(read_token(2, "pack " + fmt_pack(p) + " warnState1"),16)
            if warnState1 > 0:
                warnings += "Warning State 1: "
                for x in range(0,8):
                    if (warnState1 & (1<<x)):
                        warnings += constants.warnState1[x+1] + " | "
                warnings = warnings.rstrip("| ")
                warnings += ", "  

            warnState2 = int(read_token(2, "pack " + fmt_pack(p) + " warnState2"),16)
            if warnState2 > 0:
                warnings += "Warning State 2: "
                for x in range(0,8):
                    if (warnState2 & (1<<x)):
                        warnings += constants.warnState2[x+1] + " | "
                warnings = warnings.rstrip("| ")
                warnings += ", "  

            warnings = warnings.rstrip(", ")

            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/warnings",warnings)
            if print_initial:
                print("Pack " + fmt_pack(p) + ", warnings: " + warnings)

            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/balancing1",balanceState1)
            if print_initial:
                print("Pack " + fmt_pack(p) + ", balancing1: " + balanceState1)

            client.publish(config['mqtt_base_topic'] + "/pack_" + fmt_pack(p) + "/balancing2",balanceState2)
            if print_initial:
                print("Pack " + fmt_pack(p) + ", balancing2: " + balanceState2)

            warnings = ""

            #Test for non signed value (matching cell count), to skip possible INFOFLAG present in data
            if (byte_index + 2 <= len(inc_data)) and (cellsW != int(inc_data[byte_index:byte_index+2],16)):
                byte_index += 2

    except Exception as e:
        print("Error parsing BMS warning data: ", str(e))
        return False, "Error parsing BMS warning data: " + str(e)

    return True,True


print("Connecting to BMS...")
bms,bms_connected = bms_connect_with_retries(bms_ip,bms_port)
if bms_connected != True:
    # Startup cannot continue without BMS transport.
    sys.exit("Unable to connect to BMS after retries, exiting")

client.publish(config['mqtt_base_topic'] + "/availability","offline")
print_initial = True

# Use detected pack count to limit/guide per-pack ADR probes for version/serial.
reported_packs = 1
time.sleep(0.1)
success, data = bms_getPackNumber(bms)
if success == True:
    reported_packs = data
    print("Batteries in pack: ", data)
else:
    print("Error retrieving number of batteries in pack")

success, data = bms_getVersion(bms, reported_packs)
if success != True:
    print("Error retrieving BMS version number")

time.sleep(0.1)
success, bms_sn, pack_sn = bms_getSerial(bms, reported_packs)
if success != True:
    print("Error retrieving BMS and pack serial numbers. This is required for HA Discovery. Exiting...")
    quit()

while code_running == True:

    if bms_connected == True:
        if mqtt_connected == True:

            analog_success, data = bms_getAnalogData(bms,batNumber=255)
            if analog_success != True:
                print("Error retrieving BMS analog data: " + data)
            time.sleep(scan_interval/3)
            success, data = bms_getPackCapacity(bms)
            if success != True:
                print("Error retrieving BMS pack capacity: " + data)
            time.sleep(scan_interval/3)
            success, data = bms_getWarnInfo(bms)
            if success != True:
                print("Error retrieving BMS warning info: " + data)
            time.sleep(scan_interval/3)

            # Only publish discovery after a valid analog frame, so HA entities match real pack/cell data
            # and we avoid creating incomplete entities when multi-pack parsing fails in that cycle.
            if print_initial and analog_success:
                ha_discovery()
                
            client.publish(config['mqtt_base_topic'] + "/availability","online")

            print_initial = False
            

            repub_discovery += 1
            if repub_discovery*scan_interval > 3600:
                repub_discovery = 0
                print_initial = True
        
        else: #MQTT not connected
            client.loop_stop()
            print("MQTT disconnected, trying to reconnect...")
            if mqtt_connect():
                client.loop_start()
            time.sleep(5)
            print_initial = True
    else: #BMS not connected
        print("BMS disconnected, trying to reconnect...")
        bms,bms_connected = bms_connect_with_retries(bms_ip,bms_port)
        if bms_connected != True:
            sys.exit("Unable to reconnect to BMS after retries, exiting")
        client.publish(config['mqtt_base_topic'] + "/availability","offline")
        time.sleep(5)
        print_initial = True

graceful_shutdown("main loop exit")
