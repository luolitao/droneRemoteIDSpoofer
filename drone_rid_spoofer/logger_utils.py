import logging
from drone_rid_spoofer.state import DroneState

logger = logging.getLogger(__name__)

def log_first_packet(drone: DroneState) -> None:
    logger.info(f"📡 FIRST PACKET for {drone.serial.decode()} | "
                f"Protocol: {drone.protocol or 'astm'} | "
                f"Transport: {drone.physical_transport or 'default'}")

def log_100th_packet(drone: DroneState, counter: int) -> None:
    logger.info(f"📊 100th PACKET (#{counter}) for {drone.serial.decode()} | "
                f"Protocol: {drone.protocol or 'astm'} | "
                f"Transport: {drone.physical_transport or 'default'}")

def log_drone_params(drone: DroneState, counter: int = 0, transport_display: str = "default") -> None:
    lat = drone.lat / 1e7
    lng = drone.lng / 1e7
    logger.debug(
        f"[#{counter}] {drone.serial.decode():<20} "
        f"({lat:.6f}, {lng:.6f}) "
        f"Alt={drone.geodetic_altitude:.1f}m Spd={drone.speed:.2f}m/s "
        f"Dir={drone.direction:.1f}° "
        f"OP={drone.operator_id} "
        f"Protocol={drone.protocol or 'astm'} "
        f"Tx={transport_display}"
    )