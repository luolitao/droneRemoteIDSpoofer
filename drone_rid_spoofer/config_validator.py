# config_validator.py

import re
from typing import Dict, List, Any, Optional, Union

# 允许的协议
ALLOWED_PROTOCOLS = {"astm", "gb42590", "gb46750"}
# 允许的传输方式
ALLOWED_TRANSPORTS = {"wifi", "ble", "nan", "wifi,ble", "wifi,nan", "ble,nan", "wifi,ble,nan"}
# 允许的无人机模式
ALLOWED_MODES = {"random", "static", "waypoints"}

# GB46750 必填字段（除通用字段外）
GB46750_REQUIRED = {
    "registration_mark": str,
    "operation_category": int,
    "ua_classification": int,
    "station_location_type": int,
    "horizontal_accuracy": int,
    "vertical_accuracy": int,
    "speed_accuracy": int,
    "timestamp_accuracy": int,
}
# GB42590 必填字段（通用字段外）
GB42590_REQUIRED = {
    "operator_id": str,
}


def validate_config(config: Dict) -> List[str]:
    """Validate the entire configuration JSON.
    
    Returns a list of error messages (empty if valid).
    """
    errors = []

    # 1. 验证 global 部分
    if "global" not in config:
        errors.append("Missing 'global' section")
    else:
        errors.extend(validate_global(config["global"]))

    # 2. 验证 drones 部分
    if "drones" not in config:
        errors.append("Missing 'drones' array")
    else:
        if not isinstance(config["drones"], list):
            errors.append("'drones' must be a list")
        else:
            for idx, drone in enumerate(config["drones"]):
                errors.extend(validate_drone(drone, idx))

    return errors


def validate_global(global_conf: Dict) -> List[str]:
    errors = []
    # 必填字段
    for field in ("interface", "interval", "location"):
        if field not in global_conf:
            errors.append(f"global missing required field: {field}")
    # 检查 location 格式
    if "location" in global_conf:
        loc = global_conf["location"]
        if not isinstance(loc, list) or len(loc) != 2:
            errors.append("global.location must be [lat, lng]")
        else:
            try:
                float(loc[0]); float(loc[1])
            except (TypeError, ValueError):
                errors.append("global.location lat/lng must be numbers")
    # interval 类型
    if "interval" in global_conf and not isinstance(global_conf["interval"], (int, float)):
        errors.append("global.interval must be a number")
    # transport 可选，但若存在需合法
    if "transport" in global_conf:
        transport = global_conf["transport"]
        if not isinstance(transport, str):
            errors.append("global.transport must be a string")
        else:
            # 允许逗号分隔的组合
            parts = [p.strip() for p in transport.split(",")]
            for p in parts:
                if p not in ALLOWED_TRANSPORTS:
                    errors.append(f"global.transport contains invalid transport: {p}")
    # protocol 可选，若存在需合法
    if "protocol" in global_conf:
        if global_conf["protocol"] not in ALLOWED_PROTOCOLS:
            errors.append(f"global.protocol '{global_conf['protocol']}' not allowed")
    return errors


def validate_drone(drone: Dict, idx: int) -> List[str]:
    errors = []
    prefix = f"drones[{idx}]"

    # 检查 mode
    mode = drone.get("mode")
    if not mode:
        errors.append(f"{prefix}: missing 'mode'")
    elif mode not in ALLOWED_MODES:
        errors.append(f"{prefix}: invalid mode '{mode}'")

    # 检查 serial（可选，但若存在需长度 <=20）
    if "serial" in drone:
        if not isinstance(drone["serial"], str) or len(drone["serial"]) > 20:
            errors.append(f"{prefix}: serial must be string <=20 chars")

    # 检查 protocol
    protocol = drone.get("protocol", "astm")  # 默认 astm
    if protocol not in ALLOWED_PROTOCOLS:
        errors.append(f"{prefix}: invalid protocol '{protocol}'")

    # 检查 physical_transport（若存在）
    if "physical_transport" in drone:
        pt = drone["physical_transport"]
        parts = [p.strip() for p in pt.split(",")]
        for p in parts:
            if p not in ALLOWED_TRANSPORTS:
                errors.append(f"{prefix}: invalid physical_transport '{p}'")
    # 兼容旧 transport 字段，若为 gb42590/gb46750 则视为协议而非传输
    # 但此校验不再处理，因为映射由 spoofer 完成，但建议用户使用新字段

    # 根据 protocol 检查必要字段
    if protocol == "gb46750":
        for field, expected_type in GB46750_REQUIRED.items():
            if field not in drone:
                errors.append(f"{prefix}: GB46750 requires '{field}'")
            else:
                # 类型检查（简单）
                if not isinstance(drone[field], expected_type):
                    errors.append(f"{prefix}: '{field}' must be {expected_type.__name__}")
    elif protocol == "gb42590":
        for field, expected_type in GB42590_REQUIRED.items():
            if field not in drone:
                errors.append(f"{prefix}: GB42590 requires '{field}'")
            else:
                if not isinstance(drone[field], expected_type):
                    errors.append(f"{prefix}: '{field}' must be {expected_type.__name__}")

    # 通用字段类型检查（如果存在）
    for field, exp_type in [
        ("start_location", list), ("pilot_location", list),
        ("operator_altitude", (int, float)), ("speed", (int, float)),
        ("vertical_speed", (int, float)), ("geodetic_altitude", (int, float)),
        ("pressure_altitude", (int, float)), ("height", (int, float)),
        ("lifespan_seconds", (int, float)),
    ]:
        if field in drone and not isinstance(drone[field], exp_type):
            errors.append(f"{prefix}: '{field}' must be {exp_type.__name__}")

    # waypoints 校验
    if mode == "waypoints":
        waypoints = drone.get("waypoints")
        if not waypoints or not isinstance(waypoints, list):
            errors.append(f"{prefix}: waypoints mode requires non-empty 'waypoints' list")
        else:
            for wp_idx, wp in enumerate(waypoints):
                if not isinstance(wp, list) or len(wp) < 2:
                    errors.append(f"{prefix}: waypoints[{wp_idx}] must be [lat, lng, hold_sec?]")
                else:
                    try:
                        float(wp[0]); float(wp[1])
                    except (TypeError, ValueError):
                        errors.append(f"{prefix}: waypoints[{wp_idx}] lat/lng must be numbers")
                    if len(wp) == 3 and not isinstance(wp[2], (int, float)):
                        errors.append(f"{prefix}: waypoints[{wp_idx}] hold_sec must be a number")

    return errors