"""ASTM F3411 ODID Message Pack encoding/decoding (protocol version 1).

Exact byte layouts matching opendroneid-core-c structs (opendroneid.h).
Used by GB 42590 Wi-Fi beacon transport and sniff_gb.py.
"""

import struct
from datetime import datetime
from typing import Optional

from drone_rid_spoofer.state import DroneState

# ── Constants (matching opendroneid.h) ─────────────────────────────────

ODID_MESSAGE_SIZE = 25
ODID_PROTO_VERSION = 0x01

# Message types (page 0)
ODID_MSGTYPE_BASIC_ID = 0x0
ODID_MSGTYPE_LOCATION = 0x1
ODID_MSGTYPE_AUTH = 0x2
ODID_MSGTYPE_SELF_ID = 0x3
ODID_MSGTYPE_SYSTEM = 0x4
ODID_MSGTYPE_OPERATOR_ID = 0x5
ODID_MESSAGETYPE_PACKED = 0x0F

# ID Types
ODID_IDTYPE_SERIAL_NUMBER = 0x01
ODID_IDTYPE_CAA_REGISTRATION = 0x02
ODID_IDTYPE_UTM_ASSIGNED = 0x03
ODID_IDTYPE_SPECIFIC_SESSION = 0x04

# UA Types
ODID_UATYPE_HELICOPTER = 0x02

# Location Status
ODID_STATUS_UNDECLARED = 0x00


# ── Encoding ───────────────────────────────────────────────────────────

def encode_basic_id(serial: bytes) -> bytes:
    """Encode Basic ID message (MessageType=0x0, 25 bytes).

    Layout (ODID_BasicID_encoded):
        [0]    MessageType(4b)|ProtoVersion(4b)
        [1]    UAType(4b)|IDType(4b)
        [2:22] UASID (20 bytes, null-padded ASCII)
        [22:25] Reserved
    """
    msg = bytearray(ODID_MESSAGE_SIZE)
    msg[0] = (ODID_MSGTYPE_BASIC_ID << 4) | ODID_PROTO_VERSION
    msg[1] = (ODID_UATYPE_HELICOPTER & 0x0F) | ((ODID_IDTYPE_SERIAL_NUMBER & 0x0F) << 4)
    msg[2:22] = serial[:20].ljust(20, b'\x00')
    return bytes(msg)


def encode_location(drone: DroneState) -> bytes:
    """Encode Location message (MessageType=0x1, 25 bytes).

    Layout (ODID_Location_encoded):
        [0]    MessageType(4b)|ProtoVersion(4b)
        [1]    SpeedMult(1b)|EWDirection(1b)|HeightType(1b)|Reserved(1b)|Status(4b)
        [2]    Direction (0-179)
        [3]    SpeedHorizontal (uint8, ×0.25 m/s)
        [4]    SpeedVertical (int8, ×0.5 m/s)
        [5:9]  Latitude (int32 LE, ×10^7)
        [9:13] Longitude (int32 LE, ×10^7)
        [13:15] AltitudeBaro (uint16 LE, (m+1000)/0.5)
        [15:17] AltitudeGeo (uint16 LE, (m+1000)/0.5)
        [17:19] Height (uint16 LE, (m+1000)/0.5)
        [19]    HorizAccuracy(4b)|VertAccuracy(4b)
        [20]    SpeedAccuracy(4b)|BaroAccuracy(4b)
        [21:23] TimeStamp (uint16 LE, tenths of seconds since the hour)
        [23]    Reserved2(4b)|TSAccuracy(4b)
        [24]    Reserved3
    """
    msg = bytearray(ODID_MESSAGE_SIZE)
    msg[0] = (ODID_MSGTYPE_LOCATION << 4) | ODID_PROTO_VERSION

    # Direction: 0-179 east, 180-359 -> dir-180 west with EWDirection=1
    d = drone.direction % 360
    if d < 180:
        ew_dir, dir_byte = 0, d
    else:
        ew_dir, dir_byte = 1, d - 180

    speed_h = min(round(drone.speed / 0.25), 255)
    speed_v = max(-127, min(127, round(drone.vertical_speed / 0.5)))

    msg[1] = (0 << 0) | ((ew_dir & 0x01) << 1) | ((0 & 0x01) << 2) | (0 << 3) | ((ODID_STATUS_UNDECLARED & 0x0F) << 4)
    msg[2] = dir_byte
    msg[3] = speed_h
    msg[4] = speed_v & 0xFF

    struct.pack_into("<i", msg, 5, int(drone.lat))
    struct.pack_into("<i", msg, 9, int(drone.lng))

    for offset, val in [(13, drone.pressure_altitude), (15, drone.geodetic_altitude), (17, drone.height)]:
        v = max(0, min(0xFFFF, int(round((val + 1000.0) / 0.5))))
        struct.pack_into("<H", msg, offset, v)

    # Timestamp: tenths of seconds since the hour
    now = datetime.now()
    ts = (now.minute * 600 + now.second * 10 + now.microsecond // 100000) % 6000
    struct.pack_into("<H", msg, 21, ts)

    return bytes(msg)


def encode_self_id() -> bytes:
    """Encode Self-ID message (MessageType=0x3, 25 bytes)."""
    msg = bytearray(ODID_MESSAGE_SIZE)
    msg[0] = (ODID_MSGTYPE_SELF_ID << 4) | ODID_PROTO_VERSION
    msg[1] = 0x00  # DescType = text
    desc = b"GB Spoofer"
    msg[2:2 + len(desc)] = desc[:23]
    return bytes(msg)


def build_message_pack(drone: DroneState, send_counter: int) -> bytes:
    """Build ASTM F3411 ODID Message Pack payload for GB 42590 Wi-Fi beacon.

    Vendor IE payload structure:
        [message_counter(1)] [MessageType|ProtoVersion(1)] [SingleMsgSize=25(1)] [MsgPackSize=N(1)]
        [Message 0..N-1, each 25 bytes]

    Includes 3 messages: Basic ID, Location, Self-ID.
    """
    messages = encode_basic_id(drone.serial) + encode_location(drone) + encode_self_id()
    msg_count = 3

    header = bytearray(4)
    header[0] = send_counter & 0xFF
    header[1] = (ODID_MESSAGETYPE_PACKED << 4) | ODID_PROTO_VERSION
    header[2] = ODID_MESSAGE_SIZE
    header[3] = msg_count

    return bytes(header) + messages


# ── Decoding ───────────────────────────────────────────────────────────

def decode_message_pack(data: bytes, msg_count: int, msg_size: int) -> Optional[dict]:
    """Decode ASTM F3411 ODID Message Pack body into a flat dict.

    'data' starts at the first message (after the 3-byte header).
    """
    fields = {}
    offset = 0

    for i in range(msg_count):
        if offset + msg_size > len(data):
            return None
        msg = data[offset:offset + msg_size]
        offset += msg_size

        msg_type = msg[0] >> 4

        if msg_type == 0x00:  # Basic ID
            ua_type = msg[1] & 0x0F
            id_type = (msg[1] >> 4) & 0x0F
            uas_id = msg[2:22].rstrip(b'\x00').decode('utf-8', errors='replace')
            id_names = {0: "None", 1: "Serial", 2: "CAA", 3: "UTM", 4: "Session"}
            ua_names = {0: "None", 1: "Aeroplane", 2: "Helicopter/Multirotor", 3: "Gyroplane",
                        4: "HybridLift", 5: "Ornithopter", 6: "Glider", 7: "Kite",
                        8: "FreeBalloon", 9: "CaptiveBalloon", 10: "Airship",
                        11: "FreeFall", 12: "Rocket", 13: "Tethered", 14: "GroundObs", 15: "Other"}
            fields["Basic ID"] = f"{uas_id} (ID={id_names.get(id_type, str(id_type))}, UA={ua_names.get(ua_type, str(ua_type))})"

        elif msg_type == 0x01:  # Location
            speed_mult = (msg[1] >> 0) & 0x01
            ew_dir = (msg[1] >> 1) & 0x01
            height_type = (msg[1] >> 2) & 0x01
            status = (msg[1] >> 4) & 0x0F
            direction = msg[2] + (180 if ew_dir else 0)
            speed_h = (255 * 0.25 + msg[3] * 0.75) if speed_mult else msg[3] * 0.25
            speed_v = struct.unpack("<b", msg[4:5])[0] * 0.5
            lat = struct.unpack("<i", msg[5:9])[0] / 1e7
            lng = struct.unpack("<i", msg[9:13])[0] / 1e7
            alt_baro = struct.unpack("<H", msg[13:15])[0] * 0.5 - 1000.0
            alt_geo = struct.unpack("<H", msg[15:17])[0] * 0.5 - 1000.0
            height = struct.unpack("<H", msg[17:19])[0] * 0.5 - 1000.0
            timestamp = struct.unpack("<H", msg[21:23])[0] / 10.0

            status_names = {0: "Undeclared", 1: "Ground", 2: "Airborne", 3: "Emergency", 4: "Failure"}
            fields.update({
                "Status": status_names.get(status, f"Unknown({status})"),
                "Direction": direction,
                "Speed Horizontal": f"{speed_h:.2f} m/s",
                "Speed Vertical": f"{speed_v:.2f} m/s",
                "Latitude": lat,
                "Longitude": lng,
                "Altitude Baro": f"{alt_baro:.1f} m",
                "Altitude Geo": f"{alt_geo:.1f} m",
                "Height": f"{height:.1f} m (above {'ground' if height_type else 'takeoff'})",
                "Timestamp": f"{timestamp:.1f}s after hour",
            })

        elif msg_type == 0x03:  # Self-ID
            desc_type = msg[1]
            desc = msg[2:25].rstrip(b'\x00').decode('utf-8', errors='replace')
            desc_names = {0: "Text", 1: "Emergency", 2: "ExtendedStatus"}
            fields["Self-ID"] = f"{desc} (type={desc_names.get(desc_type, desc_type)})"

        elif msg_type == 0x04:  # System
            op_lat = struct.unpack('<i', msg[2:6])[0] / 1e7
            op_lng = struct.unpack('<i', msg[6:10])[0] / 1e7
            area_count = struct.unpack('<H', msg[10:12])[0]
            area_radius = msg[12] * 10
            area_ceiling = struct.unpack('<H', msg[13:15])[0] * 0.5 - 1000.0
            area_floor = struct.unpack('<H', msg[15:17])[0] * 0.5 - 1000.0
            op_alt_geo = struct.unpack('<H', msg[18:20])[0] * 0.5 - 1000.0
            fields["System"] = (f"op=({op_lat:.6f},{op_lng:.6f},{op_alt_geo:.1f}m) "
                                f"area=({area_count}x r={area_radius}m ceil={area_ceiling:.1f} floor={area_floor:.1f})")

        elif msg_type == 0x05:  # Operator ID
            op_id = msg[2:22].rstrip(b'\x00').decode('utf-8', errors='replace')
            fields["Operator ID"] = op_id

    return fields
