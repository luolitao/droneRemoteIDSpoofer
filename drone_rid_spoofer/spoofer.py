import argparse
import logging
import random
import select
import sys
import termios
import time
import tty
from datetime import datetime, timedelta
from typing import List, Optional

from drone_rid_spoofer.helpers import (
    generate_ble_mac,
    generate_wifi_mac,
    get_random_operator_id,
    get_random_pilot_location,
    get_random_serial_number,
    parse_location,
    random_altitude,
    random_height,
    random_location,
    random_speed,
    random_vertical_speed,
)
from drone_rid_spoofer.messages import (
    encode_basic_id,
    encode_location,
    encode_self_id,
    encode_system,
    encode_operator_id,
)
from drone_rid_spoofer.state import DroneState
from drone_rid_spoofer.transport.base import TransportBackend
from drone_rid_spoofer.transport.wifi import WifiBackend
from drone_rid_spoofer.transport.gb import GbBackend
from drone_rid_spoofer.transport.ble import BleBackend
from drone_rid_spoofer.transport.nan import NanBackend

logger = logging.getLogger(__name__)


class DroneSpoofer:
    """Main drone spoofing controller.

    GB 42590-2023 发送间隔要求：
      - 动态报文（Location）每 1 秒发送 1 次
      - 静态报文（Basic ID, Self ID, System, Operator ID）每 3 秒发送 1 次
    """

    # 静态报文类型列表（按轮转顺序）
    STATIC_MSG_TYPES = ("Basic ID", "Self ID", "System", "Operator ID")

    def __init__(self, args: argparse.Namespace, backends: List[TransportBackend]):
        self.args = args
        self.backends = backends
        self.base_location = args.location
        self._send_counters: dict = {}  # per-drone counter: serial -> int
        self._static_indices: dict = {}  # per-drone static rotation index
        self._has_wifi = any(isinstance(b, (WifiBackend, GbBackend, NanBackend))
                            for b in backends)
        self._has_ble = any(isinstance(b, BleBackend) for b in backends)
        self._setup_logging()

    def _setup_logging(self) -> None:
        level = logging.DEBUG if getattr(self.args, 'verbose', False) else logging.INFO
        logging.getLogger().setLevel(level)

    def _send(self, drone: DroneState) -> None:
        """Build messages and send via all backends.

        按 GB 42590 / ASTM F3411-22a 标准，每个 beacon 帧应包含完整的 5 条消息：
        Basic ID → Location → Self ID → System → Operator ID
        """
        key = drone.serial
        counter = self._send_counters.get(key, 0)
        self._send_counters[key] = counter + 1

        # 判断是否有 GB backend（proto=2）还是 ASTM backend（proto=2）
        has_gb = any(isinstance(b, GbBackend) for b in self.backends)
        proto = 2 if has_gb else 2

        # 构建完整的 5 条消息（按标准顺序）
        messages = [
            encode_basic_id(drone.serial, proto=proto),
            encode_location(drone, proto=proto,
                            timestamp_offset=drone.timestamp_offset),
            encode_self_id(b"GB Spoofer", proto=proto),
            encode_system(drone.pilot_location[0], drone.pilot_location[1],
                          proto=proto, operator_altitude=drone.operator_altitude),
            encode_operator_id(operator_id=drone.operator_id, proto=proto),
        ]
        msg_type = "完整 5 条消息 (Basic ID + Location + Self ID + System + Operator ID)"

        for backend in self.backends:
            backend.send_messages(drone, messages)

        self._log_drone_params(drone, msg_type, "All")

    def _get_transport_names(self) -> str:
        """返回当前激活的传输协议名称。"""
        names = []
        for b in self.backends:
            if isinstance(b, GbBackend):
                names.append("GB 42590 Wi-Fi Beacon")
            elif isinstance(b, WifiBackend):
                names.append("ASTM Wi-Fi Beacon")
            elif isinstance(b, NanBackend):
                names.append("Wi-Fi NAN")
            elif isinstance(b, BleBackend):
                names.append("BLE")
            else:
                names.append(type(b).__name__)
        return " + ".join(names)

    def _log_drone_params(self, drone: DroneState, msg_type: str = "",
                          static_name: str = "") -> None:
        """按 Remote ID 标准消息块分类输出无人机参数，去重避免重复。"""
        transport = self._get_transport_names()
        lat = drone.lat / 1e7
        lng = drone.lng / 1e7
        pilot_lat = drone.pilot_location[0] / 1e7
        pilot_lng = drone.pilot_location[1] / 1e7

        # 根据当前使用的传输类型决定显示哪些地址
        addr_parts = []
        if self._has_wifi:
            addr_parts.append(f"MAC(Wi-Fi)={drone.mac_address}")
        if self._has_ble:
            addr_parts.append(f"BLE={drone.ble_address}")
        addr_str = "  ".join(addr_parts) if addr_parts else "N/A"

        lines = [
            f"  [传输] {transport}  |  本次发送: {msg_type}",
            f"  [Basic ID]     ID={drone.serial.decode():<20} UA=Helicopter/Multirotor  "
            f"{addr_str}",
            f"  [Location]     Lat={lat:.6f}°  Lng={lng:.6f}°  "
            f"Alt(Geo)={drone.geodetic_altitude:.1f}m  Alt(Baro)={drone.pressure_altitude:.1f}m  "
            f"Height={drone.height:.1f}m  Speed(H)={drone.speed:.2f}m/s  "
            f"Speed(V)={drone.vertical_speed:.2f}m/s  Dir={drone.direction:.1f}°",
            f"  [Self ID]      Text=\"Spoofing test\"",
            f"  [System]       Pilot=({pilot_lat:.6f}, {pilot_lng:.6f})  "
            f"OpAlt={drone.operator_altitude:.1f}m  "
            f"AreaCount=0  Radius=0  Ceiling=0  Floor=0",
            f"  [Operator ID]  ID={drone.operator_id}",
        ]
        logger.info("\n".join(lines))

    def run_manual_mode(self) -> None:
        """Run controlled drone spoofing with keyboard input."""
        logger.info("Starting MANUAL MODE - Use WASD to control drone movement")

        serial = self.args.serial.encode() if self.args.serial else get_random_serial_number()
        lat, lng = random_location(*self.args.location, 10000)
        pilot_loc = get_random_pilot_location(lat, lng)
        mac_addr = generate_wifi_mac() if self._has_wifi else "00:00:00:00:00:00"
        ble_addr = generate_ble_mac() if self._has_ble else "00:00:00:00:00:00"

        drone = DroneState(serial, pilot_loc, lat, lng, mac_addr, ble_addr,
                           operator_id=get_random_operator_id(),
                           operator_altitude=random.uniform(0.0, 50.0),
                           anchor_lat=lat, anchor_lng=lng)
        self._seed_kinematics(drone)
        logger.info(f"Drone created: Serial={serial.decode()} "
                    f"Lat={lat/1e7:.6f}° Lng={lng/1e7:.6f}° "
                    f"Alt={drone.geodetic_altitude:.1f}m Speed={drone.speed:.2f}m/s "
                    f"Dir={drone.direction:.1f}°")

        self._run_manual_control_loop(drone)

    def _run_manual_control_loop(self, drone: DroneState) -> None:
        next_send = datetime.now()
        stdin_fd = sys.stdin.fileno()
        original_settings = termios.tcgetattr(stdin_fd)

        try:
            tty.setcbreak(stdin_fd)

            while True:
                if self._has_keyboard_input():
                    key = sys.stdin.read(1)
                    self._process_movement_key(drone, key)

                if datetime.now() >= next_send:
                    self._send(drone)
                    next_send = datetime.now() + timedelta(seconds=self.args.interval)

                time.sleep(self.args.interval)

        except KeyboardInterrupt:
            logger.info("Manual mode stopped by user")
        finally:
            termios.tcsetattr(stdin_fd, termios.TCSANOW, original_settings)

    def _has_keyboard_input(self) -> bool:
        return select.select([sys.stdin], [], [], 0) == ([sys.stdin], [], [])

    def _process_movement_key(self, drone: DroneState, key: str) -> None:
        key_map = {
            'w': 'north',
            's': 'south',
            'a': 'west',
            'd': 'east'
        }

        if key in key_map:
            direction = key_map[key]
            drone.move(direction, 1000)
            logger.info(f"Moved {direction.upper()}")

    def run_automatic_mode(self) -> None:
        if self.args.drones_config:
            drones = self._create_drones_from_config(self.args.drones_config)
            logger.info(f"Starting AUTOMATIC MODE - spoofing {len(drones)} drones from config")
        else:
            n_drones = max(1, self.args.random)
            logger.info(f"Starting AUTOMATIC MODE - spoofing {n_drones} drones")
            drones = self._create_drones(n_drones)

        self._run_automatic_loop(drones)

    def _create_drones(self, count: int) -> List[DroneState]:
        drones = []
        base_lat, base_lng = self.base_location

        for i in range(count):
            serial = get_random_serial_number()
            lat, lng = random_location(base_lat, base_lng, 50000)
            pilot_loc = get_random_pilot_location(lat, lng)
            mac_addr = generate_wifi_mac() if self._has_wifi else "00:00:00:00:00:00"
            ble_addr = generate_ble_mac() if self._has_ble else "00:00:00:00:00:00"

            drone = DroneState(serial, pilot_loc, lat, lng, mac_addr, ble_addr,
                               operator_id=get_random_operator_id(),
                               operator_altitude=random.uniform(0.0, 50.0),
                               anchor_lat=base_lat, anchor_lng=base_lng)
            self._seed_kinematics(drone)
            drones.append(drone)
            logger.info(f"Drone created: Serial={serial.decode()} "
                        f"Lat={lat/1e7:.6f}° Lng={lng/1e7:.6f}° "
                        f"Alt={drone.geodetic_altitude:.1f}m Speed={drone.speed:.2f}m/s "
                        f"Dir={drone.direction:.1f}°")

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

    def _create_drones_from_config(self, drones_config: List[dict]) -> List[DroneState]:
        drones = []
        base_lat, base_lng = self.base_location
        allowed_modes = {"random", "static", "waypoints"}

        for entry in drones_config:
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
            mac_addr = entry.get("mac") or (generate_wifi_mac() if self._has_wifi else "00:00:00:00:00:00")
            ble_addr = entry.get("ble_mac") or (generate_ble_mac() if self._has_ble else "00:00:00:00:00:00")
            lifespan_seconds = entry.get("lifespan_seconds", 0)
            end_time = None
            if lifespan_seconds and lifespan_seconds > 0:
                end_time = datetime.now() + timedelta(seconds=lifespan_seconds)

            drone_transport = entry.get("transport")
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
                transport=drone_transport,
                timestamp_offset=float(timestamp_offset),
                operator_id=operator_id,
                operator_altitude=operator_altitude,
                anchor_lat=anchor_lat,
                anchor_lng=anchor_lng,
            )
            self._seed_kinematics(drone, overrides=self._extract_kinematic_overrides(entry))
            drones.append(drone)
            logger.info(f"Drone created: Serial={serial_bytes.decode()} "
                        f"Lat={lat/1e7:.6f}° Lng={lng/1e7:.6f}° "
                        f"Alt={drone.geodetic_altitude:.1f}m Speed={drone.speed:.2f}m/s "
                        f"Dir={drone.direction:.1f}° Mode={mode} "
                        f"Anchor=({anchor_lat/1e7:.6f},{anchor_lng/1e7:.6f})")

        return drones

    def _extract_kinematic_overrides(self, entry: dict) -> dict:
        """Pull kinematic overrides from a scenario entry; missing keys stay random."""
        keys = ("speed", "vertical_speed", "pressure_altitude",
                "geodetic_altitude", "height")
        return {k: entry[k] for k in keys if k in entry}

    def _run_automatic_loop(self, drones: List[DroneState]) -> None:
        next_send = datetime.now()
        packet_batch_count = 0

        try:
            while True:
                if datetime.now() >= next_send:
                    now = datetime.now()
                    for drone in drones:
                        if drone.end_time and now >= drone.end_time:
                            if drone.active:
                                logger.info(f"Drone {drone.serial.decode()} expired; stopping transmission")
                                drone.active = False
                            continue
                        if not drone.active:
                            continue
                        if drone.mode == "random":
                            drone.update_location(self.args.interval)
                            drone.drift_kinematics()
                        elif drone.mode == "waypoints":
                            self._update_waypoints(drone, now)
                        self._send(drone)
                        time.sleep(0.2)

                    packet_batch_count += 1
                    active_count = sum(1 for drone in drones if drone.active)
                    logger.info(f"--- Batch {packet_batch_count} sent ({active_count} packets) ---")
                    if active_count == 0:
                        logger.info("All drones expired; stopping automatic mode")
                        break
                    next_send = datetime.now() + timedelta(seconds=self.args.interval)
                time.sleep(self.args.interval)

        except KeyboardInterrupt:
            logger.info(f"Automatic mode stopped. Sent {packet_batch_count} batches total")

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

    def _update_waypoints(self, drone: DroneState, now: datetime) -> None:
        if not drone.waypoints:
            return

        if drone.next_waypoint_time is None:
            lat, lng, hold = drone.waypoints[0]
            drone.lat = lat
            drone.lng = lng
            drone.next_waypoint_time = now + timedelta(seconds=hold)
            return

        if now < drone.next_waypoint_time:
            return

        if drone.waypoint_index < len(drone.waypoints) - 1:
            drone.waypoint_index += 1
            lat, lng, hold = drone.waypoints[drone.waypoint_index]
            drone.lat = lat
            drone.lng = lng
            drone.next_waypoint_time = now + timedelta(seconds=hold)
        else:
            drone.next_waypoint_time = now + timedelta(seconds=3600)
