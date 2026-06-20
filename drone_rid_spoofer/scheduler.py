import concurrent.futures
import logging
import time
from datetime import datetime, timedelta
from typing import List, Callable

from drone_rid_spoofer.state import DroneState

logger = logging.getLogger(__name__)

class Scheduler:
    def __init__(self, interval: float, send_callback: Callable[[DroneState], None]):
        self.interval = interval
        self.send_callback = send_callback

    def run(self, drones: List[DroneState]) -> None:
        next_send = datetime.now()
        batch_count = 0
        max_workers = min(len(drones), 20)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            try:
                while True:
                    now = datetime.now()
                    if now >= next_send:
                        active = [d for d in drones if d.active and not (d.end_time and now >= d.end_time)]
                        if not active:
                            logger.info("All drones expired; stopping automatic mode")
                            break
                        # 更新位置
                        for drone in active:
                            if drone.mode == "random":
                                drone.update_location(self.interval)
                                drone.drift_kinematics()
                            elif drone.mode == "waypoints":
                                self._update_waypoints(drone, now)
                        # 并发发送
                        futures = [executor.submit(self.send_callback, drone) for drone in active]
                        for future in concurrent.futures.as_completed(futures):
                            try:
                                future.result()
                            except Exception as e:
                                logger.error(f"Send error: {e}")
                        batch_count += 1
                        logger.debug(f"Batch {batch_count}: {len(active)} drones sent")
                        next_send = now + timedelta(seconds=self.interval)
                    time.sleep(0.01)
            except KeyboardInterrupt:
                logger.info(f"Automatic mode stopped. Sent {batch_count} batches total")

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