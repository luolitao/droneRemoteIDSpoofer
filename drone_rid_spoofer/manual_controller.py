import logging
import select
import sys
import termios
import tty
import time
from datetime import datetime, timedelta
from typing import Callable

from drone_rid_spoofer.state import DroneState

logger = logging.getLogger(__name__)

class ManualController:
    def __init__(self, interval: float, send_callback: Callable[[DroneState], None]):
        self.interval = interval
        self.send_callback = send_callback

    def run(self, drone: DroneState) -> None:
        logger.info("Starting MANUAL MODE - Use WASD to control drone movement")
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
                    self.send_callback(drone)
                    next_send = datetime.now() + timedelta(seconds=self.interval)
                time.sleep(self.interval)
        except KeyboardInterrupt:
            logger.info("Manual mode stopped by user")
        finally:
            termios.tcsetattr(stdin_fd, termios.TCSANOW, original_settings)

    def _has_keyboard_input(self) -> bool:
        return select.select([sys.stdin], [], [], 0) == ([sys.stdin], [], [])

    def _process_movement_key(self, drone: DroneState, key: str) -> None:
        key_map = {'w':'north', 's':'south', 'a':'west', 'd':'east'}
        if key in key_map:
            drone.move(key_map[key], 1000)
            logger.info(f"Moved {key_map[key].upper()}")