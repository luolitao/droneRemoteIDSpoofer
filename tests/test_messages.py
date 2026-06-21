import struct
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

from drone_rid_spoofer.messages import (
    encode_basic_id,
    encode_location,
    encode_self_id,
    encode_system,
    encode_operator_id,
    build_message_pack,
    decode_basic_id,
    decode_location,
    MsgType,
)

def test_encode_basic_id(sample_drone):
    msg = encode_basic_id(sample_drone.serial, proto=1)
    assert len(msg) == 25
    assert (msg[0] >> 4) == MsgType.BASIC_ID
    assert (msg[0] & 0x0F) == 1
    assert msg[2:22].rstrip(b'\x00') == sample_drone.serial

def test_encode_location(sample_drone):
    msg = encode_location(sample_drone, proto=1)
    assert len(msg) == 25
    assert (msg[0] >> 4) == MsgType.LOCATION
    lat = struct.unpack('<i', msg[5:9])[0]
    lng = struct.unpack('<i', msg[9:13])[0]
    assert lat == sample_drone.lat
    assert lng == sample_drone.lng

def test_decode_location(sample_drone):
    encoded = encode_location(sample_drone, proto=1)
    decoded = decode_location(encoded)
    assert decoded["Latitude"] == sample_drone.lat / 1e7
    assert decoded["Longitude"] == sample_drone.lng / 1e7

def test_self_id(sample_drone):
    msg = encode_self_id(b"Test", proto=1)
    assert len(msg) == 25
    assert (msg[0] >> 4) == MsgType.SELF_ID

def test_system(sample_drone):
    msg = encode_system(sample_drone.pilot_location[0], sample_drone.pilot_location[1])
    assert len(msg) == 25
    assert (msg[0] >> 4) == MsgType.SYSTEM

def test_operator_id(sample_drone):
    msg = encode_operator_id(sample_drone.operator_id, proto=2)
    assert len(msg) == 25
    assert (msg[0] >> 4) == MsgType.OPERATOR_ID
    assert msg[2:22].rstrip(b'\x00').decode('utf-8') == sample_drone.operator_id

def test_message_pack(sample_drone):
    messages = [
        encode_basic_id(sample_drone.serial),
        encode_location(sample_drone),
    ]
    pack = build_message_pack(messages, proto=2)
    assert pack[0] >> 4 == MsgType.PACK
    assert pack[1] == 25
    assert pack[2] == len(messages)