import argparse
import logging
import random
import select
import sys
import termios
import time
import tty
import concurrent.futures
import threading
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
from drone_rid_spoofer.transport.ble import BleBackend
from drone_rid_spoofer.transport.nan import NanBackend

import argparse
import logging
from typing import List

from drone_rid_spoofer.state import DroneState
from drone_rid_spoofer.transport.base import TransportBackend
from drone_rid_spoofer.transport.wifi import WifiBackend
from drone_rid_spoofer.transport.ble import BleBackend
from drone_rid_spoofer.transport.nan import NanBackend
from drone_rid_spoofer.messages import build_all_messages, build_gb42590_all_messages
from drone_rid_spoofer.gb46750_messages import build_gb46750_all_messages
from drone_rid_spoofer.drone_factory import DroneFactory
from drone_rid_spoofer.scheduler import Scheduler
from drone_rid_spoofer.manual_controller import ManualController
from drone_rid_spoofer.logger_utils import log_first_packet, log_100th_packet, log_drone_params

logger = logging.getLogger(__name__)


class DroneSpoofer:
<<<<<<< Updated upstream
    """Main drone spoofing controller.

    GB 42590-2023 发送间隔要求：
      - 动态报文（Location）每 1 秒发送 1 次
      - 静态报文（Basic ID, Self ID, System, Operator ID）每 3 秒发送 1 次
    """

    # 静态报文类型列表（按轮转顺序）
    STATIC_MSG_TYPES = ("Basic ID", "Self ID", "System", "Operator ID")

=======
>>>>>>> Stashed changes
    def __init__(self, args: argparse.Namespace, backends: List[TransportBackend]):
        self.args = args
        self.backends = backends
        self.base_location = args.location
        self._send_counters = {}
        self._lock = threading.Lock()
        # 构建 physical_backends 映射
        self.physical_backends = {}
        for b in backends:
            if isinstance(b, WifiBackend):
                self.physical_backends['wifi'] = b
            elif isinstance(b, NanBackend):
                self.physical_backends['nan'] = b
            elif isinstance(b, BleBackend):
                self.physical_backends['ble'] = b
        self.default_backends = backends
        self.has_wifi = any(isinstance(b, (WifiBackend, NanBackend)) for b in backends)
        self.has_ble = any(isinstance(b, BleBackend) for b in backends)
        self._setup_logging()

    def _setup_logging(self):
        level = logging.DEBUG if getattr(self.args, 'verbose', False) else logging.INFO
        logging.getLogger().setLevel(level)

    def _send(self, drone: DroneState) -> None:
<<<<<<< Updated upstream
        """Build messages and send via all backends.

        按 GB 42590 / ASTM F3411-22a 标准，每个 beacon 帧应包含完整的 5 条消息：
        Basic ID → Location → Self ID → System → Operator ID

        GB 46750 使用自有数据包格式，不需要 ASTM 消息。
        """
        key = drone.serial
        counter = self._send_counters.get(key, 0)
        self._send_counters[key] = counter + 1

        has_gb = any(isinstance(b, GB42590Backend) for b in self.backends)
        has_gb46750 = any(isinstance(b, GB46750Backend) for b in self.backends)
        proto = 2

        # Build ASTM messages (used by GB 42590, WiFi, BLE, NAN backends)
        messages = [
            encode_basic_id(drone.serial, proto=proto),
            encode_location(drone, proto=proto,
                            timestamp_offset=drone.timestamp_offset),
            encode_self_id(b"GB42590 Drone Remote ID", proto=proto),
            encode_system(drone.pilot_location[0], drone.pilot_location[1],
                          proto=proto, operator_altitude=drone.operator_altitude),
            encode_operator_id(operator_id=drone.operator_id, proto=proto),
        ]
=======
        with self._lock:
            counter = self._send_counters.get(drone.serial, 0)
            self._send_counters[drone.serial] = counter + 1
        if counter == 0:
            log_first_packet(drone)
        elif counter % 100 == 0:
            log_100th_packet(drone, counter)

        protocol = drone.protocol or "astm"
        if protocol == "gb46750":
            messages = build_gb46750_all_messages(drone)
        elif protocol == "gb42590":
            messages = build_gb42590_all_messages(drone)
        else:
            messages = build_all_messages(drone)
>>>>>>> Stashed changes

        if drone.physical_transport:
            transport_types = [t.strip() for t in drone.physical_transport.split(",")]
            backends_to_use = [self.physical_backends.get(t) for t in transport_types if self.physical_backends.get(t)]
        else:
            backends_to_use = self.default_backends

<<<<<<< Updated upstream
        # self._log_drone_params(drone)
=======
        for backend in backends_to_use:
            try:
                backend.send_messages(drone, messages, protocol)
            except Exception as e:
                logger.error(f"Send via {backend} failed: {e}")

        transport_display = drone.physical_transport or "default"
        log_drone_params(drone, counter, transport_display)
>>>>>>> Stashed changes

    def _get_transport_names(self) -> str:
        names = []
        for b in self.backends:
            if isinstance(b, WifiBackend):
                names.append("ASTM/GB Wi-Fi Beacon")
            elif isinstance(b, NanBackend):
                names.append("Wi-Fi NAN")
            elif isinstance(b, BleBackend):
                names.append("BLE")
            else:
                names.append(type(b).__name__)
        return " + ".join(names)

    def run_manual_mode(self):
        serial = self.args.serial.encode() if self.args.serial else get_random_serial_number()
        lat, lng = random_location(*self.args.location, 10000)
        pilot_loc = get_random_pilot_location(lat, lng)
        mac_addr = generate_wifi_mac() if self.has_wifi else "00:00:00:00:00:00"
        ble_addr = generate_ble_mac() if self.has_ble else "00:00:00:00:00:00"
        drone = DroneState(serial, pilot_loc, lat, lng, mac_addr, ble_addr,
                           operator_id=get_random_operator_id(),
                           operator_altitude=random.uniform(0.0, 50.0),
                           anchor_lat=lat, anchor_lng=lng)
        # 设置初始运动学
        factory = DroneFactory(self.base_location, self.has_wifi, self.has_ble)
        factory._seed_kinematics(drone)
        controller = ManualController(self.args.interval, self._send)
        controller.run(drone)

    def run_automatic_mode(self):
        factory = DroneFactory(self.base_location, self.has_wifi, self.has_ble)
        if self.args.drones_config:
            drones = factory.create_from_config(self.args.drones_config)
            logger.info(f"Starting AUTOMATIC MODE - spoofing {len(drones)} drones from config")
        else:
            n_drones = max(1, self.args.random)
            drones = factory.create_random_drones(n_drones)
            logger.info(f"Starting AUTOMATIC MODE - spoofing {n_drones} drones")
<<<<<<< Updated upstream
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

            # GB 46750-2025 specific fields
            gb46750_fields = {}
            for field in ("registration_mark", "operation_category",
                          "ua_classification", "station_location_type",
                          "horizontal_accuracy", "vertical_accuracy",
                          "speed_accuracy", "timestamp_accuracy"):
                if field in entry:
                    gb46750_fields[field] = entry[field]

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
                **gb46750_fields,
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
                    # logger.info(f"--- Batch {packet_batch_count} sent ({active_count} packets) ---")
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
=======
        scheduler = Scheduler(self.args.interval, self._send)
        scheduler.run(drones)
>>>>>>> Stashed changes
