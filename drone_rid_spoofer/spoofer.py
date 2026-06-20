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
from drone_rid_spoofer.messages import build_all_messages,build_gb42590_all_messages
from drone_rid_spoofer.gb46750_messages import build_gb46750_all_messages
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

        if drone.physical_transport:
            transport_types = [t.strip() for t in drone.physical_transport.split(",")]
            backends_to_use = [self.physical_backends.get(t) for t in transport_types if self.physical_backends.get(t)]
        else:
            backends_to_use = self.default_backends

        for backend in backends_to_use:
            try:
                backend.send_messages(drone, messages, protocol)
            except Exception as e:
                logger.error(f"Send via {backend} failed: {e}")

        transport_display = drone.physical_transport or "default"
        log_drone_params(drone, counter, transport_display)

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
        scheduler = Scheduler(self.args.interval, self._send)
        scheduler.run(drones)