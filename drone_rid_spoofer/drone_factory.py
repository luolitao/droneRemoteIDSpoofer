import random
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple
import logging

from drone_rid_spoofer.state import DroneState
from drone_rid_spoofer.helpers import (
    get_random_operator_id,
    get_random_pilot_location,
    get_random_serial_number,
    parse_location,
    random_altitude,
    random_height,
    random_location,
    random_speed,
    random_vertical_speed,
    generate_wifi_mac,
    generate_ble_mac,
)

logger = logging.getLogger(__name__)

class DroneFactory:
    def __init__(self, base_location: Tuple[int, int], has_wifi: bool, has_ble: bool):
        self.base_location = base_location
        self.has_wifi = has_wifi
        self.has_ble = has_ble

    def create_random_drones(self, count: int) -> List[DroneState]:
        drones = []
        base_lat, base_lng = self.base_location
        for _ in range(count):
            serial = get_random_serial_number()
            lat, lng = random_location(base_lat, base_lng, 50000)
            pilot_loc = get_random_pilot_location(lat, lng)
            mac_addr = generate_wifi_mac() if self.has_wifi else "00:00:00:00:00:00"
            ble_addr = generate_ble_mac() if self.has_ble else "00:00:00:00:00:00"
            drone = DroneState(
                serial=serial,
                pilot_location=pilot_loc,
                lat=lat, lng=lng,
                mac_address=mac_addr,
                ble_address=ble_addr,
                operator_id=get_random_operator_id(),
                operator_altitude=random.uniform(0.0, 50.0),
                anchor_lat=base_lat, anchor_lng=base_lng
            )
            self._seed_kinematics(drone)
            drones.append(drone)
            logger.info(f"Drone created: Serial={serial.decode()} ...")
        return drones

    def create_from_config(self, drones_config: List[dict], default_protocol: str = "astm") -> List[DroneState]:
        drones = []
        base_lat, base_lng = self.base_location
        allowed_modes = {"random", "static", "waypoints"}

        for entry in drones_config:
            # 解析 mode, waypoints, start_location, pilot_location, serial, etc.
            # 参考原 _create_drones_from_config 逻辑
            # ...（此处代码较长，保留原有逻辑，仅移入）
            # 注意：需要映射 protocol 和 physical_transport
            # 返回 DroneState 列表
            mode = entry.get("mode", "random")
            if mode not in allowed_modes:
                raise ValueError(f"Invalid drone mode '{mode}'. Allowed: {sorted(allowed_modes)}")

            waypoints = None
            if mode == "waypoints":
                waypoints = self._parse_waypoints(entry.get("waypoints", []))
                if not waypoints:
                    raise ValueError("waypoints mode requires a non-empty 'waypoints' list")

            start_location = entry.get("start_location")
            if start_location:
                if len(start_location) != 2:
                    raise ValueError("start_location must have two values: [lat, lng]")
                lat, lng = parse_location(str(start_location[0]), str(start_location[1]))
            else:
                if waypoints:
                    lat, lng, _ = waypoints[0]
                else:
                    lat, lng = random_location(base_lat, base_lng, 50000)

            pilot_location = entry.get("pilot_location")
            if pilot_location:
                if len(pilot_location) != 2:
                    raise ValueError("pilot_location must have two values: [lat, lng]")
                pilot_lat, pilot_lng = parse_location(str(pilot_location[0]), str(pilot_location[1]))
                pilot_loc = (pilot_lat, pilot_lng)
            else:
                pilot_loc = get_random_pilot_location(lat, lng)

            serial = entry.get("serial")
            serial_bytes = serial.encode() if serial else get_random_serial_number()
            mac_addr = entry.get("mac") or (generate_wifi_mac() if self.has_wifi else "00:00:00:00:00:00")
            ble_addr = entry.get("ble_mac") or (generate_ble_mac() if self.has_ble else "00:00:00:00:00:00")
            lifespan_seconds = entry.get("lifespan_seconds", 0)
            end_time = None
            if lifespan_seconds and lifespan_seconds > 0:
                end_time = datetime.now() + timedelta(seconds=lifespan_seconds)

            # 提取 transport 字段
            transport = entry.get("transport")

            # 兼容旧配置：若 transport 是 "gb42590" 或 "gb46750"，则自动设置 protocol 和 physical_transport
            if transport in ("gb42590", "gb46750"):
                protocol = transport
                physical_transport = "wifi"
            else:
                # 否则从配置中读取显式字段，若没有则使用全局默认或 None
                protocol = entry.get("protocol")  # 可能为 None
                physical_transport = entry.get("physical_transport", transport)  # 若 physical_transport 未指定，则用 transport 值

            # 如果 protocol 仍为 None，默认为 "astm"
            if protocol is None:
                protocol = "astm"

            timestamp_offset = entry.get("timestamp_offset_minutes", 0.0)
            operator_id = entry.get("operator_id", get_random_operator_id())
            
            operator_altitude = float(entry.get("operator_altitude",
                                                random.uniform(0.0, 50.0)))

            # Anchor point for boundary constraint: use start_location if provided,
            # otherwise fall back to the global scene center
            if start_location:
                anchor_lat, anchor_lng = lat, lng
            else:
                anchor_lat, anchor_lng = base_lat, base_lng

            drone = DroneState(
                serial=serial_bytes,                
                pilot_location=pilot_loc,
                lat=lat,
                lng=lng,
                mac_address=mac_addr,
                ble_address=ble_addr,
                mode=mode,
                end_time=end_time,
                waypoints=waypoints,
                protocol=protocol,
                physical_transport=physical_transport,
                timestamp_offset=float(timestamp_offset),
                operator_id=operator_id,
                operator_altitude=operator_altitude,
                anchor_lat=anchor_lat,
                anchor_lng=anchor_lng,
                registration_mark = entry.get("registration_mark"),
                operation_category=entry.get("operation_category"),
                horizontal_accuracy=entry.get("horizontal_accuracy"),
                vertical_accuracy=entry.get("vertical_accuracy"),
                speed_accuracy=entry.get("speed_accuracy"),
                timestamp_accuracy=entry.get("timestamp_accuracy")

            )
            self._seed_kinematics(drone, overrides=self._extract_kinematic_overrides(entry))
            drones.append(drone)
            logger.info(f"Drone created: Serial={serial_bytes.decode()} "
                        f"operator_id={operator_id}"
                        f"Lat={lat/1e7:.6f}° Lng={lng/1e7:.6f}° "
                        f"Alt={drone.geodetic_altitude:.1f}m Speed={drone.speed:.2f}m/s "
                        f"Dir={drone.direction:.1f}° Mode={mode} "
                        f"Anchor=({anchor_lat/1e7:.6f},{anchor_lng/1e7:.6f})")
        return drones

    def _seed_kinematics(self, drone: DroneState,
                         overrides: Optional[dict] = None) -> None:
        """Seed kinematic fields with random defaults, honoring optional overrides."""
        overrides = overrides or {}
        drone.speed = float(overrides.get("speed", random_speed()))
        drone.vertical_speed = float(
            overrides.get("vertical_speed", random_vertical_speed())
        )
        drone.geodetic_altitude = float(
            overrides.get("geodetic_altitude", random_altitude())
        )
        # Pressure altitude defaults to geodetic ± small offset for plausibility
        drone.pressure_altitude = float(
            overrides.get("pressure_altitude", drone.geodetic_altitude)
        )
        drone.height = float(overrides.get("height", random_height()))

    def _extract_kinematic_overrides(self, entry: dict) -> dict:
        """Pull kinematic overrides from a scenario entry; missing keys stay random."""
        keys = ("speed", "vertical_speed", "pressure_altitude",
                "geodetic_altitude", "height")
        return {k: entry[k] for k in keys if k in entry}

    def _parse_waypoints(self, raw_waypoints: list) -> List[tuple]:
        waypoints = []
        for entry in raw_waypoints:
            if not isinstance(entry, list) or len(entry) < 2:
                raise ValueError("Each waypoint must be [lat, lng, hold_seconds?]")
            if len(entry) > 3:
                raise ValueError("Each waypoint must be [lat, lng, hold_seconds?]")
            lat, lng = parse_location(str(entry[0]), str(entry[1]))
            hold = int(entry[2]) if len(entry) == 3 else 0
            if hold < 0:
                raise ValueError("hold_seconds must be >= 0")
            waypoints.append((lat, lng, hold))
        return waypoints