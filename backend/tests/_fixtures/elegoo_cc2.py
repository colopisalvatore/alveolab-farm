"""ELEGOO Centauri Carbon 2 protocol messages used as test fixtures.

Copied from the community protocol notes, whose samples were captured on real
CC2 firmware or derived from the official SDK:

    nict41/HACS-elegoo-integration @ 11899079, docs/CC2_PROTOCOL.md (MIT)

Line references are given per fixture. Serial numbers, IPs and MACs are
replaced by placeholders; nothing else is changed. The SDK repository itself
(elegooofficial/elegoo-link @ ec2d2269) contains no JSON samples.
"""

SERIAL = "CC2SERIAL0001"
PRINTER_IP = "192.0.2.50"

# CC2_PROTOCOL.md:308-318 — reply to {"id": 0, "method": 7000} on UDP 52700.
DISCOVERY_RESPONSE = {
    "id": 0,
    "result": {
        "host_name": "Centauri Carbon 2",
        "machine_model": "Centauri Carbon 2",
        "sn": SERIAL,
        "token_status": 0,
        "lan_status": 1,
    },
}

# First-generation Centauri Carbon (SDCP) discovery reply shape: capitalised
# Id / Data (SDK elegoo_fdm_cc_discovery_strategy.cpp:27). Must NOT parse as CC2.
CC1_DISCOVERY_RESPONSE = {
    "Id": "0a69ee780fbd40d7bfb95b312250bf46",
    "Data": {"Name": "Centauri Carbon", "MachineName": "Centauri Carbon", "MainboardID": "000000000000"},
}

# CC2_PROTOCOL.md:867-956 — full status (1002 response / first 6000 push).
FULL_STATUS_PRINTING = {
    "id": 1,
    "method": 6000,
    "result": {
        "error_code": 0,
        "machine_status": {"status": 2, "sub_status": 2075, "exception_status": [], "progress": 45},
        "print_status": {
            "filename": "benchy.gcode",
            "uuid": "<TASK_UUID>",
            "current_layer": 225,
            "total_layer": 500,
            "print_duration": 3600,
            "total_duration": 8000,
            "remaining_time_sec": 4400,
            "progress": 45,
        },
        "extruder": {"temperature": 215.0, "target": 220, "filament_detect_enable": 1, "filament_detected": 1},
        "heater_bed": {"temperature": 58.5, "target": 60},
        "ztemperature_sensor": {"temperature": 33.0, "measured_max_temperature": 0, "measured_min_temperature": 0},
        "fans": {
            "fan": {"speed": 255, "rpm": 5000},
            "aux_fan": {"speed": 178, "rpm": 3500},
            "box_fan": {"speed": 25, "rpm": 800},
            "heater_fan": {"speed": 255, "rpm": 4500},
            "controller_fan": {"speed": 255, "rpm": 4000},
        },
        "led": {"status": 1},
        "gcode_move_inf": {"x": 88.148, "y": 139.946, "z": 1.6, "e": 138.87, "speed": 9019, "speed_mode": 1},
        "toolhead": {"homed_axes": "xyz"},
        "external_device": {"camera": True, "u_disk": False, "type": "0303"},
    },
}

# CC2_PROTOCOL.md:1156-1173 — delta push merged into the cached full status.
DELTA_STATUS_PUSH = {
    "id": 42,
    "method": 6000,
    "result": {
        "error_code": 0,
        "machine_status": {"progress": 46},
        "print_status": {"current_layer": 230, "print_duration": 3650},
        "extruder": {"temperature": 219.5},
    },
}

# CC2_PROTOCOL.md:1098-1125 — attributes (1001 response).
ATTRIBUTES_RESPONSE = {
    "id": 1,
    "method": 1001,
    "result": {
        "error_code": 0,
        "hostname": "My Printer",
        "machine_model": "Centauri Carbon 2",
        "sn": SERIAL,
        "ip": PRINTER_IP,
        "mac": "<MAC>",
        "protocol_version": "1.0.0",
        "hardware_version": "1.0",
        "software_version": {"ota_version": "1.0.5.2", "mcu_version": "00.00.00.00", "soc_version": ""},
        "resolution": "1920x1080",
        "xyz_size": "220x220x250",
        "network_type": "wifi",
        "usb_connected": False,
        "camera_connected": True,
        "remaining_memory": 1073741824,
        "max_video_connections": 1,
        "video_connections": 0,
    },
}

# CC2_PROTOCOL.md:1705-1765 — CANVAS status (2005), labelled "Real-world example".
CANVAS_STATUS_RESPONSE = {
    "id": 1,
    "method": 2005,
    "result": {
        "error_code": 0,
        "canvas_info": {
            "active_canvas_id": 0,
            "active_tray_id": 3,
            "auto_refill": False,
            "canvas_list": [
                {
                    "canvas_id": 0,
                    "connected": 1,
                    "tray_list": [
                        {
                            "tray_id": 0,
                            "brand": "ELEGOO",
                            "filament_type": "PLA",
                            "filament_name": "PLA",
                            "filament_color": "#2850DF",
                            "min_nozzle_temp": 190,
                            "max_nozzle_temp": 230,
                            "status": 1,
                        },
                        {
                            "tray_id": 1,
                            "brand": "ELEGOO",
                            "filament_type": "PLA",
                            "filament_name": "PLA Basic",
                            "filament_color": "#FFFFFF",
                            "min_nozzle_temp": 190,
                            "max_nozzle_temp": 230,
                            "status": 1,
                        },
                        {
                            "tray_id": 2,
                            "brand": "ELEGOO",
                            "filament_type": "PLA",
                            "filament_name": "PLA Silk",
                            "filament_color": "#F32FF8",
                            "min_nozzle_temp": 190,
                            "max_nozzle_temp": 230,
                            "status": 1,
                        },
                        {
                            "tray_id": 3,
                            "brand": "ELEGOO",
                            "filament_type": "PLA",
                            "filament_name": "PLA",
                            "filament_color": "#000000",
                            "min_nozzle_temp": 190,
                            "max_nozzle_temp": 230,
                            "status": 2,
                        },
                    ],
                }
            ],
        },
    },
}

# CC2_PROTOCOL.md:495-518 — registration exchange.
REGISTER_OK = {"error": "ok"}
REGISTER_TOO_MANY = {"error": "too many clients"}


# --- Captured on our own Centauri 2 (29/10/2026, read-only session) ---------
# Firmware ota 02.01.01.04, LAN Only Mode on, printer idle, CANVAS connected
# with no filament info set. Serial and IP replaced by the placeholders above;
# request ids kept as sent by ElegooCC2Client. Notable vs the community notes:
# model "Centauri 2", no ztemperature_sensor (no chamber reading), tray_id
# 0-based, push ids consecutive.
REAL_DISCOVERY_RESPONSE = {
    "id": 0,
    "result": {
        "host_name": "Centauri  2",
        "lan_status": 1,
        "machine_model": "Centauri 2",
        "protocol_version": "1.0.0",
        "sn": SERIAL,
        "token_status": 1,
    },
}

REAL_FULL_STATUS_IDLE = {
    "id": 100000,
    "method": 1002,
    "result": {
        "error_code": 0,
        "external_device": {
            "auxiliary_fan": {"configured": 0},
            "camera": False,
            "chassis_camera": {"configured": 0},
            "chassis_fan": {"configured": 0},
            "chassis_lighting": {"configured": 0},
            "exhaust_grille": {"configured": 0},
            "type": "0303",
            "u_disk": False,
        },
        "extruder": {"filament_detect_enable": 1, "filament_detected": 0, "target": 0, "temperature": 39},
        "fans": {"controller_fan": {"speed": 255.0}, "fan": {"speed": 0.0}, "heater_fan": {"speed": 0.0}},
        "gcode_move": {"extruder": 0.0, "speed": 1500, "speed_mode": 1, "x": 52.5, "y": 264.0, "z": 5.420099671875},
        "heater_bed": {"target": 0, "temperature": 21},
        "machine_status": {
            "exception_status": [],
            "progress": 0,
            "status": 1,
            "sub_status": 2802,
            "sub_status_reason_code": 0,
        },
        "print_status": {
            "bed_mesh_detect": False,
            "current_layer": 0,
            "enable": False,
            "filament_detect": False,
            "filename": "",
            "print_duration": 0,
            "remaining_time_sec": 0,
            "state": "",
            "total_duration": 0,
            "uuid": "",
        },
        "tool_head": {"homed_axes": "xyz"},
    },
}

REAL_ATTRIBUTES_RESPONSE = {
    "id": 100001,
    "method": 1001,
    "result": {
        "error_code": 0,
        "hardware_version": "",
        "hostname": "Centauri  2",
        "ip": PRINTER_IP,
        "machine_model": "Centauri 2",
        "protocol_version": "1.0.0",
        "sn": SERIAL,
        "software_version": {"mcu_version": "00.00.00.00", "ota_version": "02.01.01.04", "soc_version": ""},
    },
}

REAL_CANVAS_STATUS_RESPONSE = {
    "id": 100002,
    "method": 2005,
    "result": {
        "canvas_info": {
            "active_canvas_id": 0,
            "active_tray_id": -1,
            "auto_refill": False,
            "canvas_list": [
                {
                    "canvas_id": 0,
                    "connected": 1,
                    "tray_list": [
                        {
                            "brand": "",
                            "filament_code": "",
                            "filament_color": "",
                            "filament_name": "",
                            "filament_type": "",
                            "max_nozzle_temp": 0,
                            "min_nozzle_temp": 0,
                            "status": 1,
                            "tray_id": 0,
                        },
                        {
                            "brand": "",
                            "filament_code": "",
                            "filament_color": "",
                            "filament_name": "",
                            "filament_type": "",
                            "max_nozzle_temp": 0,
                            "min_nozzle_temp": 0,
                            "status": 0,
                            "tray_id": 1,
                        },
                        {
                            "brand": "",
                            "filament_code": "",
                            "filament_color": "",
                            "filament_name": "",
                            "filament_type": "",
                            "max_nozzle_temp": 0,
                            "min_nozzle_temp": 0,
                            "status": 0,
                            "tray_id": 2,
                        },
                        {
                            "brand": "",
                            "filament_code": "",
                            "filament_color": "",
                            "filament_name": "",
                            "filament_type": "",
                            "max_nozzle_temp": 0,
                            "min_nozzle_temp": 0,
                            "status": 0,
                            "tray_id": 3,
                        },
                    ],
                }
            ],
        },
        "error_code": 0,
    },
}

REAL_DELTA_PUSHES = [
    {"id": 10, "method": 6000, "result": {"extruder": {"temperature": 40}}},
    {"id": 11, "method": 6000, "result": {"extruder": {"temperature": 39}}},
    {"id": 12, "method": 6000, "result": {"heater_bed": {"temperature": 20}}},
]
