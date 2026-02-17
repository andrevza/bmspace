# BMS Pace - Python data retrieval
Pace Battery Management System
Features:
* Compatible as a Home Assistant App, see https://www.home-assistant.io/common-tasks/os#installing-a-third-party-app-repository
* Cell voltages
* Temperatures
* State of charge (SOC)
* State of health (SOH)
* Warnings & faults
* State indications
* Cell balancing state
* and many more.....

## 1. Important

This App comes with absolutely no guarantees whatsoever. Use at own risk.  
Feel free to fork and expand!

## 2. Confirmed working with
Many brands using the PACE BMS, including:
* Greenrich U-P5000
* Hubble Lithium (AM2, AM4, X-101)
* Revov R100, R9
* SOK 48V (100Ah)
* YouthPower Rack Module 48V 100AH 4U-5U Lifepo4
* Allith 10kW LifePo4
* Joyvoit BW5KW
* etc.......

If your ports look something like this, its likely a PACE BMS:

![PACE BMS Ports](https://github.com/andrevza/bmspace/blob/main/pace-bms-ports.png?raw=true)

## 3. Configuring

### 3.1 Manually
Install the pre-requisites as per requirements.txt. Then edit the config.yaml file to suit your needs and run the script bms.py
NB: Tested with Python 3.9. Should work on later version as well.

### 3.2 Home Assistant
All configuration options are available from within Home Assistant.

Add this repository to Home Assistant with one click:

[![Add BMS Pace repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fandrevza%2Fbmspace)

### 3.3 Configuration reference
For the full and up-to-date list of App options, defaults, and optional settings, see:

* `DOCS.md`

This includes MQTT/BMS connection settings, retry behavior, parsing options, and optional topic naming controls.
For IP vs Serial setup (which fields are required for each mode), see `DOCS.md`:
* `connection_type: IP` requires `bms_ip` and `bms_port`
* `connection_type: Serial` requires `bms_serial`

## 4. RJ11 Interface (Typical, confirm your own model!)

When viewed into the RJ11 socket, tab to the bottom, pins are ordered:  
1:NC 2:GND 3:BMS_Tx 4:BMS_Rx 5:GND 6:NC

Either a direct serial interface from your hardware, a USB to serial, or a network connected TCP server device will work. 
Note the voltage levels are normal RS232 (and not TTL / 5V or something else). 

## 5. Credits

Original project and early implementation by Tertius:  
https://github.com/Tertiush/bmspace/
