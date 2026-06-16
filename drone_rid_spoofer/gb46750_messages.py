"""GB 46750-2025 Remote ID message encoding/decoding.

Implements GB 46750-2025 Section 5.2: 运行识别信息与协议 (Operational Identification
Information and Protocol).

Packet format (Figure 3 / Table 1):
  ┌──────────┬──────┬────────────────────────────────────────┐
  │ Field    │ Bytes│ Description                            │
  ├──────────┼──────┼────────────────────────────────────────┤
  │ DataType │  1   │ 255 (0xFF) — 运行识别信息数据包        │
  │ Version  │  1   │ Bits 0-2: 001; Bits 3-7: 0-63 (V1.X)  │
  │ Length   │  1   │ Total data item bytes (1-200)          │
  │ Flags    │ 3+N  │ Identifier flags with extension        │
  │ Data     │ var  │ Data items per Table 3                 │
  └──────────┴──────┴────────────────────────────────────────┘

Identifier flags (Table 2):
  Each byte: bits 0-6 = item present flag (1=send), bit 7 = extension flag
  Bit 7=0 → end of flags; Bit 7=1 → next byte is also flags

Data items (Table 3, 21 items):
  001: Unique Product ID       20B ASCII
  002: Registration Mark        8B ASCII
  003: Operation Category       1B (0=undefined,1=open,2=specific,3=certified)
  004: UA Classification        1B (0=micro,1=light,2=small,3=medium,4=large)
  005: Station Location Type    1B (0=takeoff,1=station)
  006: Station Location         8B LE lat|lng ×1e7
  007: Station Altitude         2B LE (val+1000)×2, 0.5m res
  008: UA Position              8B LE lat|lng ×1e7
  009: Track Angle              2B LE ×10, 0.1° res
  010: Ground Speed             2B LE ×10, 0.1m/s res
  011: Relative Height          2B LE (val+9000)×2, 0.5m res
  012: Vertical Speed           1B bit7=direction(0=up,1=down) bits0-6=val×2, 0.5m/s res
  013: Geodetic Altitude        2B LE (val+1000)×2, 0.5m res
  014: Barometric Altitude      2B LE (val+1000)×2, 0.5m res
  015: Operation Status         1B (0=not reported,1=ground,2=air,3=emergency,4=id fail non-emergency,5=id fail emergency)
  016: Coordinate System        1B (0=WGS-84,1=CGCS2000)
  017: Horizontal Accuracy      1B (0-12 per NACp)
  018: Vertical Accuracy        1B (0-6 per GVA)
  019: Speed Accuracy           1B (0-4 per NACv)
  020: Timestamp                6B LE Unix ms
  021: Timestamp Accuracy       1B (0-8)

References:
  - GB 46750-2025 Section 5.2
"""

import struct
import time
import logging
from enum import IntEnum
from typing import Dict, List, Optional, Tuple

from drone_rid_spoofer.state import DroneState
# ── Constants ──────────────────────────────────────────────────────────

# GB 46750 data type identifier
GB46750_DATA_TYPE = 0xFF

# Version: bits 0-2 = 001, bits 3-7 = minor version
GB46750_VERSION_BASE = 0b0010  # bits 0-3
GB46750_VERSION_MINOR = 0     # bits 4-7: V1.0

# Item IDs (001-021)
ITEM_UNIQUE_PRODUCT_ID = 1
ITEM_REGISTRATION_MARK = 2
ITEM_OPERATION_CATEGORY = 3
ITEM_UA_CLASSIFICATION = 4
ITEM_STATION_LOCATION_TYPE = 5
ITEM_STATION_LOCATION = 6
ITEM_STATION_ALTITUDE = 7
ITEM_UA_POSITION = 8
ITEM_TRACK_ANGLE = 9
ITEM_GROUND_SPEED = 10
ITEM_RELATIVE_HEIGHT = 11
ITEM_VERTICAL_SPEED = 12
ITEM_GEODETIC_ALTITUDE = 13
ITEM_BAROMETRIC_ALTITUDE = 14
ITEM_OPERATION_STATUS = 15
ITEM_COORDINATE_SYSTEM = 16
ITEM_HORIZONTAL_ACCURACY = 17
ITEM_VERTICAL_ACCURACY = 18
ITEM_SPEED_ACCURACY = 19
ITEM_TIMESTAMP = 20
ITEM_TIMESTAMP_ACCURACY = 21

# Mandatory items (M)
MANDATORY_ITEMS = {
    ITEM_UNIQUE_PRODUCT_ID,
    ITEM_REGISTRATION_MARK,
    ITEM_UA_CLASSIFICATION,
    ITEM_STATION_LOCATION_TYPE,
    ITEM_STATION_LOCATION,
    ITEM_STATION_ALTITUDE,
    ITEM_UA_POSITION,
    ITEM_TRACK_ANGLE,
    ITEM_GROUND_SPEED,
    ITEM_GEODETIC_ALTITUDE,
    ITEM_OPERATION_STATUS,
    ITEM_COORDINATE_SYSTEM,
    ITEM_HORIZONTAL_ACCURACY,
    ITEM_VERTICAL_ACCURACY,
    ITEM_SPEED_ACCURACY,
    ITEM_TIMESTAMP,
    ITEM_TIMESTAMP_ACCURACY,
}

# Optional items (O)
OPTIONAL_ITEMS = {
    ITEM_OPERATION_CATEGORY,
    ITEM_RELATIVE_HEIGHT,
    ITEM_VERTICAL_SPEED,
    ITEM_BAROMETRIC_ALTITUDE,
}


class OperationCategory(IntEnum):
    UNDEFINED = 0
    OPEN = 1
    SPECIFIC = 2
    CERTIFIED = 3


class UAClassification(IntEnum):
    MICRO = 0     # 微型
    LIGHT = 1     # 轻型
    SMALL = 2     # 小型
    MEDIUM = 3    # 中型
    LARGE = 4     # 大型


class StationLocationType(IntEnum):
    TAKEOFF = 0   # 起飞点位置
    STATION = 1   # 遥控站位置


class OperationStatus(IntEnum):
    NOT_REPORTED = 0
    GROUND = 1
    AIR = 2
    EMERGENCY = 3
    ID_FAIL_NON_EMERGENCY = 4
    ID_FAIL_EMERGENCY = 5


class CoordinateSystem(IntEnum):
    WGS84 = 0
    CGCS2000 = 1


# Item sizes in bytes (per Table 3)
ITEM_SIZES: Dict[int, int] = {
    ITEM_UNIQUE_PRODUCT_ID: 20,
    ITEM_REGISTRATION_MARK: 8,
    ITEM_OPERATION_CATEGORY: 1,
    ITEM_UA_CLASSIFICATION: 1,
    ITEM_STATION_LOCATION_TYPE: 1,
    ITEM_STATION_LOCATION: 8,
    ITEM_STATION_ALTITUDE: 2,
    ITEM_UA_POSITION: 8,
    ITEM_TRACK_ANGLE: 2,
    ITEM_GROUND_SPEED: 2,
    ITEM_RELATIVE_HEIGHT: 2,
    ITEM_VERTICAL_SPEED: 1,
    ITEM_GEODETIC_ALTITUDE: 2,
    ITEM_BAROMETRIC_ALTITUDE: 2,
    ITEM_OPERATION_STATUS: 1,
    ITEM_COORDINATE_SYSTEM: 1,
    ITEM_HORIZONTAL_ACCURACY: 1,
    ITEM_VERTICAL_ACCURACY: 1,
    ITEM_SPEED_ACCURACY: 1,
    ITEM_TIMESTAMP: 6,
    ITEM_TIMESTAMP_ACCURACY: 1,
}

# Item names for display
ITEM_NAMES: Dict[int, str] = {
    1: "唯一产品识别码",
    2: "实名登记标志",
    3: "运行类别",
    4: "UA分类",
    5: "遥控站位置类型",
    6: "遥控站位置",
    7: "遥控站高度",
    8: "UA位置",
    9: "航迹角",
    10: "地速",
    11: "相对高度",
    12: "垂直速度",
    13: "大地高度",
    14: "气压高度",
    15: "运行状态",
    16: "坐标系类型",
    17: "水平精度",
    18: "垂直精度",
    19: "速度精度",
    20: "时间戳",
    21: "时间戳精度",
}

# ── Helper: altitude encoding (GB 46750 style) ──────────────────────

def _encode_alt_gb46750(val_m: float, base: float = 1000.0) -> int:
    """Encode altitude/height as uint16: (val + base) × 2, 0.5m resolution."""
    return max(0, min(0xFFFF, int(round((val_m + base) * 2.0))))


def _decode_alt_gb46750(raw: int, base: float = 1000.0) -> float:
    """Decode altitude/height uint16 to meters."""
    return raw * 0.5 - base


# ── Flag encoding ────────────────────────────────────────────────────
def _encode_flags(item_ids: List[int]) -> bytes:
    """Encode identifier flags per Table 2.

    根据协议标准 (表2) 的实际掩码映射关系：
    - 每个字节中的 items 1-7 对应 bit 7 到 bit 1 (即 0x80 到 0x02)。
    - 扩展标志位对应 bit 0 (即 0x01)。
      (Bit 0 = 1 表示后面还有标识字节，0 表示这是最后一个字节)。

    21 个数据项分布在标识字节中：
      字节 1 (bits 7-1): items 1-7,   bit 0: 扩展标志
      字节 2 (bits 7-1): items 8-14,  bit 0: 扩展标志
      字节 3 (bits 7-1): items 15-21, bit 0: 扩展标志
    """
    flags = []
    for group_start in (1, 8, 15):
        byte_val = 0
        has_any = False
        for bit_idx in range(7):
            item_id = group_start + bit_idx
            if item_id in item_ids:
                # 映射关系：item 1 -> bit 7 (0x80), item 2 -> bit 6 (0x40) ... item 7 -> bit 1 (0x02)
                byte_val |= (1 << (7 - bit_idx))
                has_any = True
                
        if has_any or group_start <= 15:  # 始终包含必选的组 (此处逻辑保持原样)
            flags.append(byte_val)

    # 设置扩展标志位：除了最后一个字节外，其余字节的 bit 0 均置为 1 (0x01)
    result = bytearray()
    for i, f in enumerate(flags):
        if i < len(flags) - 1:
            # 非最后一个字节，设置扩展位 0x01
            result.append(f | 0x01)
        else:
            # 最后一个字节，确保扩展位为 0 (0xFE 即二进制的 1111 1110)
            result.append(f & 0xFE)
            
    return bytes(result)

def _decode_flags(data: bytes, offset: int = 0) -> Tuple[List[int], int]:
    """Decode identifier flags, return (list of item IDs, new offset).
    
    根据协议标准 (表2) 的实际掩码映射关系：
    - 每个字节中的 items 1-7 对应 bit 7 到 bit 1 (即 0x80 到 0x02)。
    - 扩展标志位对应 bit 0 (即 0x01)。
    """
    item_ids = []
    group_start = 1
    
    while offset < len(data):
        byte_val = data[offset]
        offset += 1
        
        # 1. 解析数据内容项 (bit 7 到 bit 1)
        for bit_idx in range(7):
            # 映射关系：bit_idx=0 对应 bit 7 (0x80), bit_idx=6 对应 bit 1 (0x02)
            if byte_val & (1 << (7 - bit_idx)):
                item_id = group_start + bit_idx
                if item_id <= 21:
                    item_ids.append(item_id)
                    
        # 2. 检查扩展标志位 (bit 0)
        # 如果 bit 0 为 0，说明这是最后一个标识字节，结束解析
        if not (byte_val & 0x01):  
            break
            
        group_start += 7
        
    return item_ids, offset


# ── Individual item encoders ─────────────────────────────────────────
def _encode_unique_product_id(serial: bytes) -> bytes:
    """001: 20-byte ASCII product ID, big-endian, NULL-padded."""
    return serial[:20].ljust(20, b'\x00')


def _encode_registration_mark(mark: str) -> bytes:
    """002: 8-byte ASCII registration mark (last 8 chars), NULL-padded."""
    return mark.encode('ascii', errors='replace')[-8:].ljust(8, b'\x00')


def _encode_operation_category(cat: int) -> bytes:
    """003: 1-byte operation category."""
    return bytes([cat & 0x0F])


def _encode_ua_classification(cls_: int) -> bytes:
    """004: 1-byte UA classification."""
    return bytes([cls_ & 0x0F])


def _encode_station_location_type(typ: int) -> bytes:
    """005: 1-byte station location type."""
    return bytes([typ & 0x0F])


def _encode_station_location(lat: int, lng: int) -> bytes:
    """006: 8-byte LE lat|lng ×1e7. Unknown → 0xFFFFFFFF."""
    if lat is None or lng is None:
        return struct.pack('<I', 0xFFFFFFFF) + struct.pack('<I', 0xFFFFFFFF)
    return struct.pack('<ii', lng, lat)


def _encode_station_altitude(alt_m: float) -> bytes:
    """007: 2-byte LE (val+1000)×2, 0.5m res. Unknown → 0."""
    return struct.pack('<H', _encode_alt_gb46750(alt_m, 1000.0))


def _encode_ua_position(lat: int, lng: int) -> bytes:
    if lat is None or lng is None:
        return struct.pack('<I', 0xFFFFFFFF) + struct.pack('<I', 0xFFFFFFFF)
    return struct.pack('<ii', lng, lat)


def _encode_track_angle(deg: float) -> bytes:
    """009: 2-byte LE ×10, 0.1° res. Unknown → 0xFFFF."""
    if deg is None:
        return b'\xff\xff'
    val = int(deg * 10) % 3600
    return struct.pack('<H', val)


def _encode_ground_speed(mps: float) -> bytes:
    """010: 2-byte LE ×10, 0.1m/s res. Unknown → 0xFFFF."""
    if mps is None:
        return b'\xff\xff'
    val = max(0, min(0xFFFE, int(mps * 10)))
    return struct.pack('<H', val)


def _encode_relative_height(h_m: float) -> bytes:
    """011: 2-byte LE (val+9000)×2, 0.5m res. Unknown → 0."""
    if h_m is None:
        return b'\x00\x00'
    return struct.pack('<H', _encode_alt_gb46750(h_m, 9000.0))


def _encode_vertical_speed(mps: float) -> bytes:
    """012: 1-byte bit7=direction(0=up,1=down) bits0-6=val×2. Unknown → 0xFF."""
    if mps is None:
        return b'\xff'
    direction = 1 if mps < 0 else 0
    val = min(127, int(abs(mps) * 2))
    return bytes([(direction << 7) | val])


def _encode_geodetic_altitude(alt_m: float) -> bytes:
    """013: 2-byte LE (val+1000)×2, 0.5m res. Unknown → 0."""
    if alt_m is None:
        return b'\x00\x00'
    return struct.pack('<H', _encode_alt_gb46750(alt_m, 1000.0))


def _encode_barometric_altitude(alt_m: float) -> bytes:
    """014: 2-byte LE (val+1000)×2, 0.5m res. Unknown → 0."""
    if alt_m is None:
        return b'\x00\x00'
    return struct.pack('<H', _encode_alt_gb46750(alt_m, 1000.0))


def _encode_operation_status(status: int) -> bytes:
    """015: 1-byte operation status."""
    return bytes([status & 0x0F])


def _encode_coordinate_system(sys_: int) -> bytes:
    """016: 1-byte coordinate system type."""
    return bytes([sys_ & 0x0F])


def _encode_horizontal_accuracy(acc: int) -> bytes:
    """017: 1-byte horizontal accuracy (NACp)."""
    return bytes([acc & 0x0F])


def _encode_vertical_accuracy(acc: int) -> bytes:
    """018: 1-byte vertical accuracy (GVA)."""
    return bytes([acc & 0x0F])


def _encode_speed_accuracy(acc: int) -> bytes:
    """019: 1-byte speed accuracy (NACv)."""
    return bytes([acc & 0x0F])


def _encode_timestamp(ts_ms: int = None) -> bytes:
    """020: 6-byte LE Unix timestamp in ms. Unknown → 0."""
    if ts_ms is None:
        ts_ms = int(time.time() * 1000)
    return struct.pack('<Q', ts_ms)[:6]  # LE 48-bit


def _decode_timestamp_6le(data: bytes) -> int:
    """Decode 6-byte LE timestamp to ms."""
    return struct.unpack('<Q', data.ljust(8, b'\x00'))[0]


def _encode_timestamp_accuracy(acc: int) -> bytes:
    """021: 1-byte timestamp accuracy."""
    return bytes([acc & 0x0F])


# ── Item encoder dispatch ────────────────────────────────────────────

_ITEM_ENCODERS = {
    ITEM_UNIQUE_PRODUCT_ID: _encode_unique_product_id,
    ITEM_REGISTRATION_MARK: _encode_registration_mark,
    ITEM_OPERATION_CATEGORY: _encode_operation_category,
    ITEM_UA_CLASSIFICATION: _encode_ua_classification,
    ITEM_STATION_LOCATION_TYPE: _encode_station_location_type,
    ITEM_STATION_LOCATION: _encode_station_location,
    ITEM_STATION_ALTITUDE: _encode_station_altitude,
    ITEM_UA_POSITION: _encode_ua_position,
    ITEM_TRACK_ANGLE: _encode_track_angle,
    ITEM_GROUND_SPEED: _encode_ground_speed,
    ITEM_RELATIVE_HEIGHT: _encode_relative_height,
    ITEM_VERTICAL_SPEED: _encode_vertical_speed,
    ITEM_GEODETIC_ALTITUDE: _encode_geodetic_altitude,
    ITEM_BAROMETRIC_ALTITUDE: _encode_barometric_altitude,
    ITEM_OPERATION_STATUS: _encode_operation_status,
    ITEM_COORDINATE_SYSTEM: _encode_coordinate_system,
    ITEM_HORIZONTAL_ACCURACY: _encode_horizontal_accuracy,
    ITEM_VERTICAL_ACCURACY: _encode_vertical_accuracy,
    ITEM_SPEED_ACCURACY: _encode_speed_accuracy,
    ITEM_TIMESTAMP: _encode_timestamp,
    ITEM_TIMESTAMP_ACCURACY: _encode_timestamp_accuracy,
}


# ── Full packet builder ──────────────────────────────────────────────

def build_gb46750_packet(
    serial: bytes,
    registration_mark: str = "",
    operation_category: int = OperationCategory.UNDEFINED,
    ua_classification: int = UAClassification.MICRO,
    station_location_type: int = StationLocationType.TAKEOFF,
    station_lat: int = None,
    station_lng: int = None,
    station_altitude: float = 0.0,
    ua_lat: int = 0,
    ua_lng: int = 0,
    track_angle: float = None,
    ground_speed: float = None,
    relative_height: float = None,
    vertical_speed: float = None,
    geodetic_altitude: float = 0.0,
    barometric_altitude: float = None,
    operation_status: int = OperationStatus.AIR,
    coordinate_system: int = CoordinateSystem.WGS84,
    horizontal_accuracy: int = 0,
    vertical_accuracy: int = 0,
    speed_accuracy: int = 0,
    timestamp_ms: int = None,
    timestamp_accuracy: int = 0,
    include_optional: bool = True,
) -> bytes:
    """Build a complete GB 46750-2025 data packet.

    Returns the full packet bytes ready for embedding in the Vendor IE.
    """
    # Determine which items to include
    item_ids = list(MANDATORY_ITEMS)
    if include_optional:
        item_ids.extend(OPTIONAL_ITEMS)
    item_ids.sort()

    # Build item values dict
    item_values = {
        ITEM_UNIQUE_PRODUCT_ID: serial,
        ITEM_REGISTRATION_MARK: registration_mark,
        ITEM_OPERATION_CATEGORY: operation_category,
        ITEM_UA_CLASSIFICATION: ua_classification,
        ITEM_STATION_LOCATION_TYPE: station_location_type,
        ITEM_STATION_LOCATION: (station_lat, station_lng),
        ITEM_STATION_ALTITUDE: station_altitude,
        ITEM_UA_POSITION: (ua_lat, ua_lng),
        ITEM_TRACK_ANGLE: track_angle,
        ITEM_GROUND_SPEED: ground_speed,
        ITEM_RELATIVE_HEIGHT: relative_height,
        ITEM_VERTICAL_SPEED: vertical_speed,
        ITEM_GEODETIC_ALTITUDE: geodetic_altitude,
        ITEM_BAROMETRIC_ALTITUDE: barometric_altitude,
        ITEM_OPERATION_STATUS: operation_status,
        ITEM_COORDINATE_SYSTEM: coordinate_system,
        ITEM_HORIZONTAL_ACCURACY: horizontal_accuracy,
        ITEM_VERTICAL_ACCURACY: vertical_accuracy,
        ITEM_SPEED_ACCURACY: speed_accuracy,
        ITEM_TIMESTAMP: timestamp_ms,
        ITEM_TIMESTAMP_ACCURACY: timestamp_accuracy,
    }

    # Encode data items in order
    data_parts = []
    for item_id in item_ids:
        encoder = _ITEM_ENCODERS.get(item_id)
        if encoder is None:
            continue
        value = item_values.get(item_id)
        if isinstance(value, tuple):
            data_parts.append(encoder(*value))
        else:
            data_parts.append(encoder(value))

    data_body = b''.join(data_parts)

    # Build flags
    flags = _encode_flags(item_ids)

    # Build version byte: bits 0-2 = 001, bits 3-7 = minor version
    version_byte = (GB46750_VERSION_BASE << 4) | (GB46750_VERSION_MINOR )

    # Assemble full packet
    packet = bytearray()
    packet.append(GB46750_DATA_TYPE)   # DataType = 0xFF
    packet.append(version_byte)        # Version
    packet.append(len(data_body))      # Length
    packet.extend(flags)               # Identifier flags
    packet.extend(data_body)           # Data items
    return bytes(packet)


def decode_gb46750_packet(packet: bytes) -> Optional[Dict]:
    """Decode a GB 46750-2025 data packet into a dict."""
    if len(packet) < 4:
        return None

    data_type = packet[0]
    if data_type != GB46750_DATA_TYPE:
        return None

    version_byte = packet[1]
    version_major = 1
    version_minor = (version_byte >> 3) & 0x1F
    version = f"V{version_major}.{version_minor}"

    data_length = packet[2]

    # Decode flags
    item_ids, offset = _decode_flags(packet, 3)

    result = {
        "_type": "GB46750-2025",
        "_version": version,
        "_data_length": data_length,
    }

    data_start = offset
    for item_id in item_ids:
        size = ITEM_SIZES.get(item_id, 0)
        if data_start + size > len(packet):
            break
        item_data = packet[data_start:data_start + size]
        name = ITEM_NAMES.get(item_id, f"Item{item_id:03d}")

        if item_id == ITEM_UNIQUE_PRODUCT_ID:
            result[name] = item_data.rstrip(b'\x00').decode('ascii', errors='replace')
        elif item_id == ITEM_REGISTRATION_MARK:
            result[name] = item_data.rstrip(b'\x00').decode('ascii', errors='replace')
        elif item_id == ITEM_OPERATION_CATEGORY:
            cats = {0: "未定义", 1: "开放类", 2: "特定类", 3: "审定类"}
            result[name] = cats.get(item_data[0], f"未知({item_data[0]})")
        elif item_id == ITEM_UA_CLASSIFICATION:
            classes = {0: "微型", 1: "轻型", 2: "小型", 3: "中型", 4: "大型"}
            result[name] = classes.get(item_data[0], f"未知({item_data[0]})")
        elif item_id == ITEM_STATION_LOCATION_TYPE:
            types = {0: "起飞点", 1: "遥控站"}
            result[name] = types.get(item_data[0], f"未知({item_data[0]})")
        elif item_id in (ITEM_STATION_LOCATION, ITEM_UA_POSITION):
            lat = struct.unpack('<i', item_data[0:4])[0]
            lng = struct.unpack('<i', item_data[4:8])[0]
            result[name] = f"({lat/1e7:.6f}, {lng/1e7:.6f})"
        elif item_id == ITEM_STATION_ALTITUDE:
            result[name] = f"{_decode_alt_gb46750(struct.unpack('<H', item_data)[0], 1000.0):.1f}m"
        elif item_id == ITEM_TRACK_ANGLE:
            val = struct.unpack('<H', item_data)[0]
            result[name] = f"{val/10.0:.1f}°" if val != 0xFFFF else "未知"
        elif item_id == ITEM_GROUND_SPEED:
            val = struct.unpack('<H', item_data)[0]
            result[name] = f"{val/10.0:.1f}m/s" if val != 0xFFFF else "未知"
        elif item_id == ITEM_RELATIVE_HEIGHT:
            result[name] = f"{_decode_alt_gb46750(struct.unpack('<H', item_data)[0], 9000.0):.1f}m"
        elif item_id == ITEM_VERTICAL_SPEED:
            if item_data[0] == 0xFF:
                result[name] = "未知"
            else:
                direction = "↓" if (item_data[0] & 0x80) else "↑"
                val = (item_data[0] & 0x7F) * 0.5
                result[name] = f"{val:.1f}m/s {direction}"
        elif item_id == ITEM_GEODETIC_ALTITUDE:
            result[name] = f"{_decode_alt_gb46750(struct.unpack('<H', item_data)[0], 1000.0):.1f}m"
        elif item_id == ITEM_BAROMETRIC_ALTITUDE:
            result[name] = f"{_decode_alt_gb46750(struct.unpack('<H', item_data)[0], 1000.0):.1f}m"
        elif item_id == ITEM_OPERATION_STATUS:
            statuses = {0: "未报告", 1: "地面", 2: "空中", 3: "紧急状态", 4: "ID失效(非紧急)", 5: "ID失效(紧急)"}
            result[name] = statuses.get(item_data[0], f"未知({item_data[0]})")
        elif item_id == ITEM_COORDINATE_SYSTEM:
            systems = {0: "WGS-84", 1: "CGCS2000"}
            result[name] = systems.get(item_data[0], f"未知({item_data[0]})")
        elif item_id == ITEM_HORIZONTAL_ACCURACY:
            nacp = {0: "≥18.52km/未知", 1: "<18.52km", 2: "<7.41km", 3: "<3.70km",
                    4: "<1852m", 5: "<926m", 6: "<556m", 7: "<185m",
                    8: "<92.6m", 9: "<30m", 10: "<10m", 11: "<3m", 12: "<1m"}
            result[name] = nacp.get(item_data[0], f"未知({item_data[0]})")
        elif item_id == ITEM_VERTICAL_ACCURACY:
            gva = {0: "≥150m/未知", 1: "<150m", 2: "<45m", 3: "<25m",
                   4: "<10m", 5: "<3m", 6: "<1m"}
            result[name] = gva.get(item_data[0], f"未知({item_data[0]})")
        elif item_id == ITEM_SPEED_ACCURACY:
            nacv = {0: "≥10m/s/未知", 1: "<10m/s", 2: "<3m/s", 3: "<1m/s", 4: "<0.3m/s"}
            result[name] = nacv.get(item_data[0], f"未知({item_data[0]})")
        elif item_id == ITEM_TIMESTAMP:
            ts = _decode_timestamp_6le(item_data)
            result[name] = f"{ts}ms ({time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(ts/1000))} UTC)"
        elif item_id == ITEM_TIMESTAMP_ACCURACY:
            tacc = {0: ">0.5s/未知", 1: "≤0.5s", 2: "≤0.4s", 3: "≤0.3s",
                    4: "≤0.2s", 5: "≤0.1s", 6: "≤50ms", 7: "≤20ms", 8: "≤10ms"}
            result[name] = tacc.get(item_data[0], f"未知({item_data[0]})")
        else:
            result[name] = item_data.hex()

        data_start += size

    return result


def build_gb46750_all_messages(drone: DroneState) -> List[bytes]:
    # logging.info(f"drone.timestamp_offset", {drone.end_time})
    return [build_gb46750_packet(serial = drone.serial,
                                registration_mark=drone.registration_mark,
                                operation_category=drone.operation_category,
                                ua_classification=drone.ua_classification,
                                station_lat= drone.anchor_lat,
                                station_lng = drone.anchor_lng,
                                station_altitude = drone.operator_altitude,
                                ua_lat = drone.lat,
                                ua_lng = drone.lng,
                                track_angle = drone.direction,
                                ground_speed = drone.speed,
                                relative_height = drone.height,
                                vertical_speed = drone.vertical_speed,
                                geodetic_altitude = drone.geodetic_altitude,
                                barometric_altitude = drone.pressure_altitude,
                                operation_status = OperationStatus.AIR,
                                coordinate_system = CoordinateSystem.WGS84,
                                horizontal_accuracy = drone.horizontal_accuracy,
                                vertical_accuracy = drone.vertical_accuracy,
                                speed_accuracy = drone.speed_accuracy,
                                timestamp_ms = None,
                                timestamp_accuracy = drone.timestamp_accuracy,
                                include_optional = True,)]
    