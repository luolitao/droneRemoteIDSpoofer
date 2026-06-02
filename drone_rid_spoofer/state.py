import math
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Tuple, List, Optional

from drone_rid_spoofer.helpers import drift


@dataclass
class DroneState:
    """Represents the state of a single drone."""
    serial: bytes
    pilot_location: Tuple[int, int]
    lat: int
    lng: int
    mac_address: str       # Wi-Fi unicast LAA
    ble_address: str        # BLE Static Random
    direction: float = 0.0  # degrees (0=north, 90=east, etc.), now float for smooth drift
    mode: str = "random"
    end_time: Optional[datetime] = None
    active: bool = True
    waypoints: Optional[List[Tuple[int, int, int]]] = None
    waypoint_index: int = 0
    next_waypoint_time: Optional[datetime] = None
    transport: Optional[str] = None  # per-drone transport override
    timestamp_offset: float = 0.0  # minutes to shift ASTM timestamp (negative = past)
    speed: float = 0.0              # horizontal speed (m/s)
    vertical_speed: float = 0.0     # vertical speed (m/s, + = climbing)
    pressure_altitude: float = 0.0  # meters MSL
    geodetic_altitude: float = 0.0  # meters MSL
    height: float = 0.0             # meters above takeoff/ground
    operator_id: str = ""           # operator registration ID (CAA, etc.)
    operator_altitude: float = 0.0  # operator altitude in meters (MSL)
    anchor_lat: int = 0             # anchor point lat (int32×10⁻⁷) for boundary constraint
    anchor_lng: int = 0             # anchor point lng (int32×10⁻⁷) for boundary constraint
    max_roam_radius_m: float = 1000.0  # max distance from anchor (meters)

    # GB 46750-2025 specific fields
    registration_mark: str = ""     # 实名登记标志 (8 chars max)
    operation_category: int = 0     # 运行类别 (0=未定义,1=开放类,2=特定类,3=审定类)
    ua_classification: int = 0      # UA分类 (0=微型,1=轻型,2=小型,3=中型,4=大型)
    station_location_type: int = 0  # 遥控站位置类型 (0=起飞点,1=遥控站)
    horizontal_accuracy: int = 0    # 水平精度 (NACp, 0-12)
    vertical_accuracy: int = 0      # 垂直精度 (GVA, 0-6)
    speed_accuracy: int = 0         # 速度精度 (NACv, 0-4)
    timestamp_accuracy: int = 0     # 时间戳精度 (0-8)

    # Earth constants (WGS-84)
    _METERS_PER_DEG_LAT = 111320.0  # meters per degree of latitude

    def _distance_to_anchor(self) -> float:
        """Haversine distance (meters) from current position to anchor point."""
        lat1 = math.radians(self.anchor_lat / 10**7)
        lng1 = math.radians(self.anchor_lng / 10**7)
        lat2 = math.radians(self.lat / 10**7)
        lng2 = math.radians(self.lng / 10**7)
        dlat = lat2 - lat1
        dlng = lng2 - lng1
        a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2)**2
        return 6371000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def _bearing_to_anchor(self) -> float:
        """Bearing (degrees, 0=north) from current position toward anchor point."""
        lat1 = math.radians(self.lat / 10**7)
        lng1 = math.radians(self.lng / 10**7)
        lat2 = math.radians(self.anchor_lat / 10**7)
        lng2 = math.radians(self.anchor_lng / 10**7)
        dlng = lng2 - lng1
        y = math.sin(dlng) * math.cos(lat2)
        x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlng)
        return (math.degrees(math.atan2(y, x)) + 360) % 360

    def update_location(self, interval_seconds: float) -> None:
        """Update drone location based on speed + direction over the given interval.

        Direction drifts randomly ±30° per tick for natural-looking movement.
        When approaching the roam boundary, direction is biased toward the anchor
        point to keep the drone within the allowed radius.
        """
        dist_to_anchor = self._distance_to_anchor()
        boundary_ratio = dist_to_anchor / self.max_roam_radius_m

        # Drift direction randomly ±30° per update for realistic flight behaviour
        self.direction = (self.direction + random.uniform(-30.0, 30.0)) % 360.0

        # When near or beyond the boundary, bias direction toward anchor
        if boundary_ratio > 0.7:
            bearing_to_anchor = self._bearing_to_anchor()
            # Blend: closer to boundary → stronger pull toward anchor
            pull_weight = min(1.0, (boundary_ratio - 0.7) / 0.3)  # 0.7→0.0, 1.0→1.0
            # Weighted average of bearings, handling the 0°/360° wrap
            diff = ((bearing_to_anchor - self.direction + 180) % 360) - 180
            self.direction = (self.direction + diff * pull_weight) % 360

        # Calculate distance travelled in this interval
        distance_m = self.speed * interval_seconds

        # Decompose into north/east components
        direction_rad = math.radians(self.direction)
        delta_north = distance_m * math.cos(direction_rad)  # meters north
        delta_east = distance_m * math.sin(direction_rad)   # meters east

        # Convert meters to int32×10⁻⁷ coordinate units
        delta_lat = int(delta_north / self._METERS_PER_DEG_LAT * 10**7)
        # Longitude degree width varies with latitude
        meters_per_deg_lng = self._METERS_PER_DEG_LAT * math.cos(math.radians(self.lat / 10**7))
        delta_lng = int(delta_east / meters_per_deg_lng * 10**7)

        self.lat += delta_lat
        self.lng += delta_lng

    def drift_kinematics(self) -> None:
        """Drift speed/vertical speed/altitudes/height by realistic small steps.

        Altitude capped at 120 m AGL (GB 42590 requirement).
        """
        self.speed = drift(self.speed, 1.5, 0.0, 60.0)
        self.vertical_speed = drift(self.vertical_speed, 0.8, -10.0, 10.0)
        self.geodetic_altitude = drift(self.geodetic_altitude, 5.0, 0.0, 120.0)
        # Pressure altitude tracks geodetic with a small offset
        self.pressure_altitude = drift(self.pressure_altitude, 5.0, 0.0, 120.0)
        self.height = drift(self.height, 5.0, 0.0, 120.0)

    def move(self, direction: str, step: int) -> None:
        """Move drone in specified direction and update rotation."""
        direction_map = {
            'north': (step, 0, 0.0),
            'south': (-step, 0, 180.0),
            'east': (0, step, 90.0),
            'west': (0, -step, 270.0)
        }

        if direction in direction_map:
            lat_delta, lng_delta, rotation = direction_map[direction]
            self.lat += lat_delta
            self.lng += lng_delta
            self.direction = rotation
