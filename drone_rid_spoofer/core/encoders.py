import struct
from enum import IntEnum
from datetime import datetime
from drone_rid_spoofer.state import DroneState

MESSAGE_SIZE = 25

class MsgType(IntEnum):
    BASIC_ID = 0x0
    LOCATION = 0x1
    AUTH = 0x2
    SELF_ID = 0x3
    SYSTEM = 0x4
    OPERATOR_ID = 0x5
    PACK = 0xF

def _clamp_alt(val_m: float) -> int:
    return max(0, min(0xFFFF, int(round((val_m + 100.0) / 0.5))))

def _clamp_timestamp() -> int:
    now = datetime.now()
    return (now.minute * 600 + now.second * 10 + now.microsecond // 100000) % 6000

def _clamp_speed_h(mps: float) -> int:
    return max(0, min(255, int(round(mps / 0.25))))

def _clamp_speed_v(mps: float) -> int:
    return max(-127, min(127, int(round(mps / 0.5))))

def _clamp_direction(deg: int) -> tuple:
    d = deg % 360
    return (0, d) if d < 180 else (1, d - 180)

def encode_basic_id(serial: bytes, proto: int = 2, id_type: int = 1, ua_type: int = 2) -> bytes:
    """纯粹生成 25 字节的 Basic ID 消息"""
    msg = bytearray(MESSAGE_SIZE)
    msg[0] = (MsgType.BASIC_ID << 4) | (proto & 0x0F)
    if proto == 1:
        msg[1] = (ua_type & 0x0F) | ((id_type & 0x0F) << 4)
    else:
        msg[1] = (ua_type << 4) | (id_type & 0x0F)  # GB42590/v2: 交换高低 4 位
    msg[2:22] = serial[:20].ljust(20, b'\x00')
    return bytes(msg)

def encode_location(drone: DroneState, proto: int = 2, status: int = 2, height_type: int = 0) -> bytes:
    """纯粹生成 25 字节的 Location 消息"""
    msg = bytearray(MESSAGE_SIZE)
    msg[0] = (MsgType.LOCATION << 4) | (proto & 0x0F)
    ew_dir, dir_byte = _clamp_direction(int(drone.direction))
    
    if proto == 1:
        msg[1] = (ew_dir << 1) | (height_type << 2) | (status << 4)
    else:
        msg[1] = (ew_dir << 5) | (height_type << 4) | (status & 0x0F)  # GB42590/v2 独特的比特排布
        
    msg[2] = dir_byte
    msg[3] = _clamp_speed_h(drone.speed)
    msg[4] = _clamp_speed_v(drone.vertical_speed) & 0xFF
    struct.pack_into("<i", msg, 5, int(drone.lat))
    struct.pack_into("<i", msg, 9, int(drone.lng))
    struct.pack_into("<H", msg, 13, _clamp_alt(drone.pressure_altitude))
    struct.pack_into("<H", msg, 15, _clamp_alt(drone.geodetic_altitude))
    struct.pack_into("<H", msg, 17, _clamp_alt(drone.height))
    struct.pack_into("<H", msg, 21, _clamp_timestamp())
    return bytes(msg)


def encode_gb_location(drone) -> bytes:
    """
    根据 GB 42590 表 4 规范严格封装位置向量报文 (包含高度缩放与时间戳)
    """
    msg_header = b'\x01' # 国标位置向量报头

    # === 字节 1: 运行状态与标志位 ===
    operational_status = 2 << 4  # 7-4位：运行状态 (2=InFlight)
    alt_type = 1 << 2            # 2位：高度类型 (1=几何高度)
    
    heading = int(getattr(drone, 'direction', 0)) % 360
    ew_flag = 0
    if heading >= 180:
        ew_flag = 1 << 1
        heading_val = heading - 180
    else:
        heading_val = heading
    speed_multiplier = 0
    byte_1 = operational_status | alt_type | ew_flag | speed_multiplier

    # === 字节 2-4: 航迹角、地速、垂直速度 ===
    # 航迹角按照国标转换为 0-179 的编码
    byte_2_heading = heading_val & 0xFF
    
    # 地速：国标和ASTM一致，以 0.25 m/s 为单位缩放（若超出则用速度乘数，这里简单乘以 4）
    speed_val = int(getattr(drone, 'speed', 0) / 0.25) & 0xFF
    
    # 垂直速度：以 0.5 m/s 为单位缩放
    v_speed_val = int(getattr(drone, 'vertical_speed', 0) / 0.5) & 0xFF

    # === 字节 5-12: 纬度、经度 (小端序 4 字节 int32) ===
    lat_val = int(getattr(drone, 'lat', 0))
    lng_val = int(getattr(drone, 'lng', 0))

    # === 字节 13-18: 高度编码 【核心修正：国标高度缩放公式】 ===
    # 编码值 = (实际高度以米为单位 + 1000) / 0.5
    def calc_gb_alt(alt_meters):
        val = int((float(alt_meters) + 1000.0) / 0.5)
        return max(0, min(val, 0xFFFF))

    press_alt = calc_gb_alt(getattr(drone, 'pressure_altitude', 150.0))
    geo_alt = calc_gb_alt(getattr(drone, 'geodetic_altitude', 150.0))
    
    # 距地高度 (Height) 编码公式：实际高度 / 0.5 (不需要加 1000)
    height_val = int(max(0.0, float(getattr(drone, 'height', 50.0))) / 0.5) & 0xFFFF

    # === 字节 19-20: 水平/垂直精度、速度精度 ===
    # 7-4位垂直精度 (例如2代表均匀<10m)，3-0位水平精度 (例如1代表均匀<10m)
    horiz_vert_acc = (2 << 4) | 2  
    speed_acc = 1

    # === 字节 21-24: 时间戳、时间戳精度、预留 ===
    now = datetime.now()
    seconds_this_hour = (now.minute * 60) + now.second + (now.microsecond / 1000000.0)
    timestamp_val = int(seconds_this_hour * 10) & 0xFFFF
    timestamp_acc = 1 # 0.1秒精度
    reserved_byte = 0

    # 组装 24 字节内容
    payload_24 = struct.pack(
        '<BBBBiiHHHBBHBB',
        byte_1, byte_2_heading, speed_val, v_speed_val,
        lat_val, lng_val,
        press_alt, geo_alt, height_val,
        horiz_vert_acc, speed_acc,
        timestamp_val, timestamp_acc, reserved_byte
    )

    return msg_header + payload_24

# 类似地，在这里放 encode_self_id, encode_system, encode_operator_id
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

def encode_operator_id(operator_id: str = "", proto: int = 2,
                       operator_id_type: int = 0) -> bytes:
    """Encode Operator ID message (MessageType=0x5, 25 bytes).

    Layout per opendroneid-core-c (ASTM F3411-22a):
      [MsgType|Proto(1)] [OperatorIdType(1)] [OperatorId(20)] [Reserved(3)]

    OperatorIdType: 0 = Operator ID (CAA registration or equivalent).
    OperatorId is a 20-byte UTF-8 string.
    """
    msg = bytearray(MESSAGE_SIZE)
    msg[0] = (MsgType.OPERATOR_ID << 4) | (proto & 0x0F)
    msg[1] = operator_id_type & 0xFF
    # Write operator ID into bytes 2-21 (20 bytes max)
    op_id_bytes = operator_id.encode('utf-8')[:20].ljust(20, b'\x00')
    msg[2:22] = op_id_bytes
    # bytes 22-24 are reserved (already zero from bytearray init)
    return bytes(msg)


def decode_operator_id(msg: bytes) -> Dict:
    """Decode an Operator ID message (25 bytes) into a dict."""
    operator_id_type = msg[1]
    type_names = {0: "Operator ID"}
    op_id = msg[2:22].rstrip(b'\x00').decode('utf-8', errors='replace')
    return {"Operator ID": f"{op_id} (type={type_names.get(operator_id_type, str(operator_id_type))})"}