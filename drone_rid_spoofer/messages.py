"""ASTM F3411 Remote ID message encoding/decoding.

Design inspired by tuupola/librid: each message type has its own
init/encode/decode functions with clamping (not rejecting) out-of-range values.
Supports protocol versions 1 (opendroneid-core-c style) and 2 (legacy style).
"""

import struct
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Dict, List, Optional, Tuple

from drone_rid_spoofer.state import DroneState

# ── Constants ──────────────────────────────────────────────────────────

MESSAGE_SIZE = 25
PACK_MAX_MESSAGES = 9

class MsgType(IntEnum):
    BASIC_ID = 0x0
    LOCATION = 0x1
    AUTH = 0x2
    SELF_ID = 0x3
    SYSTEM = 0x4
    OPERATOR_ID = 0x5
    PACK = 0xF

# ID Types
IDTYPE_NONE = 0x00
IDTYPE_SERIAL_NUMBER = 0x01
IDTYPE_CAA_REGISTRATION = 0x02
IDTYPE_UTM_ASSIGNED = 0x03
IDTYPE_SPECIFIC_SESSION = 0x04

# UA Types
UATYPE_NONE = 0x00
UATYPE_AEROPLANE = 0x01
UATYPE_HELICOPTER = 0x02
UATYPE_GYROPLANE = 0x03
UATYPE_HYBRID_LIFT = 0x04
UATYPE_ORNITHOPTER = 0x05
UATYPE_GLIDER = 0x06
UATYPE_KITE = 0x07
UATYPE_FREE_BALLOON = 0x08
UATYPE_CAPTIVE_BALLOON = 0x09
UATYPE_AIRSHIP = 0x0A
UATYPE_FREE_FALL = 0x0B
UATYPE_ROCKET = 0x0C
UATYPE_TETHERED_POWERED = 0x0D
UATYPE_GROUND_OBSTACLE = 0x0E
UATYPE_OTHER = 0x0F

# Location Status
STATUS_UNDECLARED = 0x00
STATUS_GROUND = 0x01
STATUS_AIRBORNE = 0x02
STATUS_EMERGENCY = 0x03
STATUS_FAILURE = 0x04

# Named lookup tables
IDTYPE_NAMES = {0: "None", 1: "Serial", 2: "CAA", 3: "UTM", 4: "Session"}
UATYPE_NAMES = {
    0: "None", 1: "Aeroplane", 2: "Helicopter/Multirotor", 3: "Gyroplane",
    4: "HybridLift", 5: "Ornithopter", 6: "Glider", 7: "Kite",
    8: "FreeBalloon", 9: "CaptiveBalloon", 10: "Airship",
    11: "FreeFall", 12: "Rocket", 13: "Tethered", 14: "GroundObs", 15: "Other",
}
STATUS_NAMES = {0: "Undeclared", 1: "Ground", 2: "Airborne", 3: "Emergency", 4: "Failure"}


# ── Helper: altitude/height encoding ────────────────────────────────────

def _clamp_alt(val_m: float) -> int:
    """Encode altitude/height (m) as uint16: (val + 1000) / 0.5, clamped to [0, 65535]."""
    return max(0, min(0xFFFF, int(round((val_m + 1000.0) / 0.5))))


def _decode_alt(raw: int) -> float:
    """Decode altitude/height uint16 to meters."""
    return raw * 0.5 - 1000.0


def _clamp_timestamp() -> int:
    """Current time as tenths of seconds since the hour (0-5999)."""
    now = datetime.now()
    return (now.minute * 600 + now.second * 10 + now.microsecond // 100000) % 6000


def _clamp_speed_h(mps: float) -> int:
    """Encode horizontal speed (m/s) as uint8 ×0.25, clamped [0, 255]."""
    return max(0, min(255, int(round(mps / 0.25))))


def _clamp_speed_v(mps: float) -> int:
    """Encode vertical speed (m/s) as int8 ×0.5, clamped [-127, 127]."""
    return max(-127, min(127, int(round(mps / 0.5))))


def _decode_speed_h(raw: int, speed_mult: int = 0) -> float:
    """Decode horizontal speed uint8 to m/s."""
    return (255 * 0.25 + raw * 0.75) if speed_mult else raw * 0.25


def _decode_speed_v(raw: int) -> float:
    """Decode vertical speed int8 to m/s."""
    return raw * 0.5


def _clamp_direction(deg: int) -> Tuple[int, int]:
    """Split 0-359 direction into EWDirection bit + 0-179 byte."""
    d = deg % 360
    return (0, d) if d < 180 else (1, d - 180)


def _decode_direction(raw: int, ew_dir: int) -> int:
    """Recombine direction byte + EW bit into 0-359."""
    return raw + (180 if ew_dir else 0)


# ── Basic ID ────────────────────────────────────────────────────────────

def encode_basic_id(serial: bytes, proto: int = 1,
                    id_type: int = IDTYPE_SERIAL_NUMBER,
                    ua_type: int = UATYPE_HELICOPTER) -> bytes:
    """Encode Basic ID message (MessageType=0x0).

    Layout: [MsgType|Proto(1)] [UAType|IDType(1)] [UASID(20)] [Reserved(3)]
    """
    msg = bytearray(MESSAGE_SIZE)
    msg[0] = (MsgType.BASIC_ID << 4) | (proto & 0x0F)
    if proto == 1:
        msg[1] = (ua_type & 0x0F) | ((id_type & 0x0F) << 4)
    else:
        msg[1] = (ua_type << 4) | (id_type & 0x0F)  # v2: swapped nibbles
    msg[2:22] = serial[:20].ljust(20, b'\x00')
    return bytes(msg)


def decode_basic_id(msg: bytes) -> Dict:
    """Decode a Basic ID message (25 bytes) into a dict."""
    id_type = (msg[1] >> 4) & 0x0F if (msg[0] & 0x0F) == 1 else msg[1] & 0x0F
    ua_type = msg[1] & 0x0F if (msg[0] & 0x0F) == 1 else (msg[1] >> 4) & 0x0F
    uas_id = msg[2:22].rstrip(b'\x00').decode('utf-8', errors='replace')
    return {"Basic ID": f"{uas_id} (ID={IDTYPE_NAMES.get(id_type, str(id_type))}, "
                        f"UA={UATYPE_NAMES.get(ua_type, str(ua_type))})"}


# ── Location ────────────────────────────────────────────────────────────

def encode_location(drone: DroneState, proto: int = 1,
                    status: int = STATUS_UNDECLARED,
                    height_type: int = 0,
                    timestamp_offset: float = 0.0) -> bytes:
    """Encode Location message (MessageType=0x1).

    Layout (25 bytes): MsgType|Proto, Status flags, Direction, SpeedH,
    SpeedV, Lat(4 LE), Lng(4 LE), AltBaro(2), AltGeo(2), Height(2),
    Accuracy(2), Timestamp(2), Reserved(2).
    """
    msg = bytearray(MESSAGE_SIZE)
    msg[0] = (MsgType.LOCATION << 4) | (proto & 0x0F)

    ew_dir, dir_byte = _clamp_direction(drone.direction)
    speed_h = _clamp_speed_h(drone.speed)
    speed_v = _clamp_speed_v(drone.vertical_speed)

    if proto == 1:
        msg[1] = ((0 & 0x01) << 0) | ((ew_dir & 0x01) << 1) | \
                 ((height_type & 0x01) << 2) | (0 << 3) | ((status & 0x0F) << 4)
        msg[2] = dir_byte
        msg[3] = speed_h
        msg[4] = speed_v & 0xFF
        struct.pack_into("<i", msg, 5, int(drone.lat))
        struct.pack_into("<i", msg, 9, int(drone.lng))
        for off, val in [(13, drone.pressure_altitude), (15, drone.geodetic_altitude), (17, drone.height)]:
            struct.pack_into("<H", msg, off, _clamp_alt(val))
        struct.pack_into("<H", msg, 21, _clamp_timestamp())
    else:
        # v2: different byte 1 layout: EW bit at bit5, rest similar
        msg[1] = ((ew_dir & 0x01) << 5) | ((height_type & 0x01) << 4) | (status & 0x0F)
        msg[2] = dir_byte
        msg[3] = speed_h
        msg[4] = speed_v & 0xFF
        struct.pack_into("<i", msg, 5, int(drone.lat))
        struct.pack_into("<i", msg, 9, int(drone.lng))
        struct.pack_into("<H", msg, 13, _clamp_alt(drone.pressure_altitude))
        struct.pack_into("<H", msg, 15, _clamp_alt(drone.geodetic_altitude))
        struct.pack_into("<H", msg, 17, _clamp_alt(drone.height))
        struct.pack_into("<H", msg, 21, _clamp_timestamp())

    return bytes(msg)


def decode_location(msg: bytes) -> Dict:
    """Decode a Location message (25 bytes) into a dict."""
    proto = msg[0] & 0x0F
    if proto == 1:
        speed_mult = (msg[1] >> 0) & 0x01
        ew_dir = (msg[1] >> 1) & 0x01
        height_type = (msg[1] >> 2) & 0x01
        status = (msg[1] >> 4) & 0x0F
    else:
        status = msg[1] & 0x0F
        height_type = (msg[1] >> 4) & 0x01
        ew_dir = (msg[1] >> 5) & 0x01
        speed_mult = 0

    direction = _decode_direction(msg[2], ew_dir)
    speed_h = _decode_speed_h(msg[3], speed_mult)
    speed_v = _decode_speed_v(struct.unpack("<b", msg[4:5])[0])
    lat = struct.unpack("<i", msg[5:9])[0] / 1e7
    lng = struct.unpack("<i", msg[9:13])[0] / 1e7

    return {
        "Status": STATUS_NAMES.get(status, f"Unknown({status})"),
        "Direction": direction,
        "Speed Horizontal": f"{speed_h:.2f} m/s",
        "Speed Vertical": f"{speed_v:.2f} m/s",
        "Latitude": lat,
        "Longitude": lng,
        "Altitude Baro": f"{_decode_alt(struct.unpack('<H', msg[13:15])[0]):.1f} m",
        "Altitude Geo": f"{_decode_alt(struct.unpack('<H', msg[15:17])[0]):.1f} m",
        "Height": f"{_decode_alt(struct.unpack('<H', msg[17:19])[0]):.1f} m "
                  f"(above {'ground' if height_type else 'takeoff'})",
        "Timestamp": f"{struct.unpack('<H', msg[21:23])[0] / 10.0:.1f}s after hour",
    }


# ── Self ID ─────────────────────────────────────────────────────────────

def encode_self_id(description: bytes = b"GB Spoofer", proto: int = 1,
                   desc_type: int = 0) -> bytes:
    """Encode Self-ID message (MessageType=0x3, 25 bytes)."""
    msg = bytearray(MESSAGE_SIZE)
    msg[0] = (MsgType.SELF_ID << 4) | (proto & 0x0F)
    msg[1] = desc_type & 0xFF
    body = description[:23].ljust(23, b'\x00')
    msg[2:25] = body
    return bytes(msg)


def decode_self_id(msg: bytes) -> Dict:
    """Decode a Self-ID message (25 bytes) into a dict."""
    desc_type = msg[1]
    desc = msg[2:25].rstrip(b'\x00').decode('utf-8', errors='replace')
    type_names = {0: "Text", 1: "Emergency", 2: "ExtendedStatus"}
    return {"Self-ID": f"{desc} (type={type_names.get(desc_type, desc_type)})"}


# ── System ──────────────────────────────────────────────────────────────

def encode_system(pilot_lat: int, pilot_lng: int, proto: int = 1,
                  operator_altitude: float = 0.0) -> bytes:
    """Encode System message (MessageType=0x4, 25 bytes).

    Layout: [MsgType|Proto(1)] [OpLocType(1)] [OpLat(4 LE)] [OpLng(4 LE)]
            [AreaCount(2)] [AreaRadius(1)] [AreaCeiling(2)] [AreaFloor(2)]
            [Classification(1)] [OpAlt(2)] [Timestamp(2)]
    """
    msg = bytearray(MESSAGE_SIZE)
    msg[0] = (MsgType.SYSTEM << 4) | (proto & 0x0F)
    msg[1] = 0x05  # operator location type: live GNSS, airborne
    struct.pack_into("<i", msg, 2, pilot_lat)
    struct.pack_into("<i", msg, 6, pilot_lng)
    msg[16] = 0x12  # classification: EU category specific
    # Operator altitude (bytes 18-19): encode same as drone altitude
    struct.pack_into("<H", msg, 18, _clamp_alt(operator_altitude))
    return bytes(msg)


def decode_system(msg: bytes) -> Dict:
    """Decode a System message (25 bytes) into a dict."""
    op_lat = struct.unpack('<i', msg[2:6])[0] / 1e7
    op_lng = struct.unpack('<i', msg[6:10])[0] / 1e7
    area_count = struct.unpack('<H', msg[10:12])[0]
    area_radius = msg[12] * 10
    area_ceiling = _decode_alt(struct.unpack('<H', msg[13:15])[0])
    area_floor = _decode_alt(struct.unpack('<H', msg[15:17])[0])
    op_alt = _decode_alt(struct.unpack('<H', msg[18:20])[0])
    return {"System": (f"op=({op_lat:.6f},{op_lng:.6f},{op_alt:.1f}m) "
                       f"area=({area_count}x r={area_radius}m ceil={area_ceiling:.1f} floor={area_floor:.1f})")}


# ── Operator ID ─────────────────────────────────────────────────────────

def encode_operator_id(operator_id: str = "", proto: int = 1) -> bytes:
    """Encode Operator ID message (MessageType=0x5, 25 bytes).

    Layout: [MsgType|Proto(1)] [Reserved(1)] [OperatorID(20)] [Reserved(3)]
    OperatorID is a 20-byte UTF-8 string (typically a CAA registration ID).
    """
    msg = bytearray(MESSAGE_SIZE)
    msg[0] = (MsgType.OPERATOR_ID << 4) | (proto & 0x0F)
    # Write operator ID into bytes 2-21 (20 bytes max)
    op_id_bytes = operator_id.encode('utf-8')[:20].ljust(20, b'\x00')
    msg[2:22] = op_id_bytes
    return bytes(msg)


def decode_operator_id(msg: bytes) -> Dict:
    """Decode an Operator ID message (25 bytes) into a dict."""
    op_id = msg[2:22].rstrip(b'\x00').decode('utf-8', errors='replace')
    return {"Operator ID": op_id}


# ── Message-level dispatch ──────────────────────────────────────────────

_DECODERS = {
    0x0: decode_basic_id,
    0x1: decode_location,
    0x3: decode_self_id,
    0x4: decode_system,
    0x5: decode_operator_id,
}


def decode_message(msg: bytes) -> Optional[Dict]:
    """Decode a single 25-byte message into a dict. Returns None if unknown type."""
    msg_type = msg[0] >> 4
    decoder = _DECODERS.get(msg_type)
    return decoder(msg) if decoder else None


# ── Message Pack (PACK type, 0xF) ───────────────────────────────────────

def build_message_pack(messages: List[bytes], proto: int = 2) -> bytes:
    """Build a standard Message Pack (type 0xF) from individual messages.

    Layout: [MsgType|Proto(1)] [SingleMsgSize=25(1)] [MsgCount(1)] [Messages...]
    Used by Wi-Fi NAN and standard Wi-Fi Beacon transports.
    """
    header = bytes([
        (MsgType.PACK << 4) | (proto & 0x0F),
        MESSAGE_SIZE,
        len(messages) & 0xFF,
    ])
    return header + b''.join(messages)


def build_gb_pack(drone: DroneState, send_counter: int, proto: int = 1) -> bytes:
    """Build GB 42590 vendor IE payload with counter + Message Pack.

    Layout: [counter(1)] [MsgType|Proto(1)] [MsgSize=25(1)] [MsgCount=5(1)]
            [BasicID(25)] [Location(25)] [SelfID(25)] [System(25)] [OperatorID(25)]

    Includes pilot/operator information per GB 42590-2023 requirements.
    """
    messages = (
        encode_basic_id(drone.serial, proto=proto)
        + encode_location(drone, proto=proto)
        + encode_self_id(proto=proto)
        + encode_system(drone.pilot_location[0], drone.pilot_location[1],
                        proto=proto, operator_altitude=drone.operator_altitude)
        + encode_operator_id(operator_id=drone.operator_id, proto=proto)
    )
    msg_count = 5
    header = bytes([
        send_counter & 0xFF,
        (MsgType.PACK << 4) | (proto & 0x0F),
        MESSAGE_SIZE,
        msg_count,
    ])
    return header + messages


def decode_message_pack(data: bytes, msg_count: int, msg_size: int = MESSAGE_SIZE) -> Optional[Dict]:
    """Decode a Message Pack body (after the 3-byte header) into a flat dict."""
    fields = {}
    offset = 0
    for _ in range(msg_count):
        if offset + msg_size > len(data):
            return None
        msg = data[offset:offset + msg_size]
        offset += msg_size
        result = decode_message(msg)
        if result:
            fields.update(result)
    return fields


# ── All-messages builder (for standard transports: wifi, ble, nan) ──────

def build_all_messages(drone: DroneState, proto: int = 2) -> List[bytes]:
    """Build all ASTM message payloads for a drone (protocol v2 by default).

    Includes: Basic ID, Location, Self ID, System (pilot location + altitude),
    Operator ID.
    """
    return [
        encode_basic_id(drone.serial, proto=proto),
        encode_location(drone, proto=proto, timestamp_offset=drone.timestamp_offset),
        encode_self_id(b"Spoofing test", proto=proto),
        encode_system(drone.pilot_location[0], drone.pilot_location[1],
                      proto=proto, operator_altitude=drone.operator_altitude),
        encode_operator_id(operator_id=drone.operator_id, proto=proto),
    ]
