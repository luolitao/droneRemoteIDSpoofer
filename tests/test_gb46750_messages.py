from drone_rid_spoofer.gb46750_messages import build_gb46750_packet, decode_gb46750_packet
import pytest
from drone_rid_spoofer.state import DroneState

@pytest.fixture
def sample_drone():
    return DroneState(
        serial=b"TEST_SERIAL_01",
        pilot_location=(231403000, 1132725000),
        lat=231405000,
        lng=1132727000,
        mac_address="02:00:00:00:00:01",
        ble_address="02:00:00:00:00:02",
        operator_id="OP-12345",
        operator_altitude=25.0,
        anchor_lat=231403000,
        anchor_lng=1132725000,
        speed=5.0,
        vertical_speed=0.5,
        geodetic_altitude=100.0,
        pressure_altitude=102.0,
        height=50.0,
        direction=45.0,
        registration_mark="UAS0001",
        operation_category=1,
        ua_classification=0,
        station_location_type=1,
        horizontal_accuracy=11,
        vertical_accuracy=5,
        speed_accuracy=2,
        timestamp_accuracy=4,
    )

def test_build_gb46750_packet(sample_drone):
    packet = build_gb46750_packet(
        serial=sample_drone.serial,
        registration_mark="UAS0001",
        operation_category=1,
        ua_classification=0,
        station_location_type=1,
        station_lat=sample_drone.anchor_lat,
        station_lng=sample_drone.anchor_lng,
        station_altitude=sample_drone.operator_altitude,
        ua_lat=sample_drone.lat,
        ua_lng=sample_drone.lng,
        track_angle=sample_drone.direction,
        ground_speed=sample_drone.speed,
        relative_height=sample_drone.height,
        vertical_speed=sample_drone.vertical_speed,
        geodetic_altitude=sample_drone.geodetic_altitude,
        barometric_altitude=sample_drone.pressure_altitude,
    )
    assert packet[0] == 0xFF  # DataType
    assert len(packet) > 10
    # 检查是否包含期望的 flags
    decoded = decode_gb46750_packet(packet)
    assert decoded is not None
    assert decoded["实名登记标志"] == "UAS0001"