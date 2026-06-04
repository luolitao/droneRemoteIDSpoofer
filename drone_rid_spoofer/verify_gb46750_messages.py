#!/usr/bin/env python3
"""GB 46750-2025 消息编码/解码与 Beacon 帧构造验证脚本。

验证 GB 46750-2025 Section 5.2 定义的数据包格式：
  - 数据包结构（DataType, Version, Length, Flags, Data Items）
  - 21 个数据项的编码正确性
  - 标识位编码/解码
  - Wi-Fi Beacon Vendor IE 帧构造

Usage:
    python3 verify_gb46750_messages.py              # 运行所有测试
    python3 verify_gb46750_messages.py --verbose    # 详细输出
"""

import argparse
import struct
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from drone_rid_spoofer.gb46750_messages import (
    build_gb46750_packet,
    decode_gb46750_packet,
    _encode_flags,
    _decode_flags,
    _encode_alt_gb46750,
    _decode_alt_gb46750,
    _encode_unique_product_id,
    _encode_registration_mark,
    _encode_operation_category,
    _encode_ua_classification,
    _encode_station_location_type,
    _encode_station_location,
    _encode_station_altitude,
    _encode_ua_position,
    _encode_track_angle,
    _encode_ground_speed,
    _encode_relative_height,
    _encode_vertical_speed,
    _encode_geodetic_altitude,
    _encode_barometric_altitude,
    _encode_operation_status,
    _encode_coordinate_system,
    _encode_horizontal_accuracy,
    _encode_vertical_accuracy,
    _encode_speed_accuracy,
    _encode_timestamp,
    _encode_timestamp_accuracy,
    _decode_timestamp_6le,
    GB46750_DATA_TYPE,
    ITEM_UNIQUE_PRODUCT_ID,
    ITEM_REGISTRATION_MARK,
    ITEM_OPERATION_CATEGORY,
    ITEM_UA_CLASSIFICATION,
    ITEM_STATION_LOCATION_TYPE,
    ITEM_STATION_LOCATION,
    ITEM_STATION_ALTITUDE,
    ITEM_UA_POSITION,
    ITEM_TRACK_ANGLE,
    ITEM_GROUND_SPEED,
    ITEM_RELATIVE_HEIGHT,
    ITEM_VERTICAL_SPEED,
    ITEM_GEODETIC_ALTITUDE,
    ITEM_BAROMETRIC_ALTITUDE,
    ITEM_OPERATION_STATUS,
    ITEM_COORDINATE_SYSTEM,
    ITEM_HORIZONTAL_ACCURACY,
    ITEM_VERTICAL_ACCURACY,
    ITEM_SPEED_ACCURACY,
    ITEM_TIMESTAMP,
    ITEM_TIMESTAMP_ACCURACY,
    ITEM_SIZES,
    OperationCategory,
    UAClassification,
    StationLocationType,
    OperationStatus,
    CoordinateSystem,
    MANDATORY_ITEMS,
    OPTIONAL_ITEMS,
)

from drone_rid_spoofer.state import DroneState

# ── Test helpers ─────────────────────────────────────────────────────────

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> bool:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ✅ {name}: {detail}" if detail else f"  ✅ {name}")
    else:
        FAILED += 1
        print(f"  ❌ {name}: {detail}" if detail else f"  ❌ {name}")
    return condition


def section(title: str):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def summary():
    total = PASSED + FAILED
    print(f"\n{'='*70}")
    print(f"  RESULTS: {PASSED}/{total} passed, {FAILED}/{total} failed")
    print(f"{'='*70}")
    if FAILED > 0:
        sys.exit(1)


# ── Test 1: Flag encoding/decoding ───────────────────────────────────────

section("Test 1: 标识位编码/解码 (Identifier Flags)")

# All 17 mandatory items
all_items = sorted(MANDATORY_ITEMS)
flags = _encode_flags(all_items)
check("Flags 至少 3 字节 (17 items, 每组 7 bits)", len(flags) >= 3, f"got {len(flags)}")

# Decode back
decoded, end_off = _decode_flags(flags, 0)
check("解码回全部 mandatory items", set(decoded) == MANDATORY_ITEMS,
      f"expected {len(MANDATORY_ITEMS)}, got {len(decoded)}")

# All items including optional
all_with_opt = sorted(MANDATORY_ITEMS | OPTIONAL_ITEMS)
flags_all = _encode_flags(all_with_opt)
decoded_all, _ = _decode_flags(flags_all, 0)
check("解码回全部 21 items", set(decoded_all) == (MANDATORY_ITEMS | OPTIONAL_ITEMS),
      f"expected 21, got {len(decoded_all)}")

# Extension bits (bit 0 = 1 means more bytes follow)
check("第1字节 bit0=1 (有扩展)", (flags_all[0] & 0x01) != 0)
check("第2字节 bit0=1 (有扩展)", (flags_all[1] & 0x01) != 0)
check("第3字节 bit0=0 (无扩展)", (flags_all[2] & 0x01) == 0)


# ── Test 2: Altitude encoding ────────────────────────────────────────────

section("Test 2: 高度编码")

check("0m → (0+1000)×2 = 2000", _encode_alt_gb46750(0.0, 1000.0) == 2000)
check("100m → (100+1000)×2 = 2200", _encode_alt_gb46750(100.0, 1000.0) == 2200)
check("500m → (500+1000)×2 = 3000", _encode_alt_gb46750(500.0, 1000.0) == 3000)
check("-500m → (-500+1000)×2 = 1000", _encode_alt_gb46750(-500.0, 1000.0) == 1000)
check("decode 2000 → 0m", abs(_decode_alt_gb46750(2000, 1000.0) - 0.0) < 0.01)
check("decode 3000 → 500m", abs(_decode_alt_gb46750(3000, 1000.0) - 500.0) < 0.01)

# Relative height uses base=9000
check("0m → (0+9000)×2 = 18000", _encode_alt_gb46750(0.0, 9000.0) == 18000)
check("100m → (100+9000)×2 = 18200", _encode_alt_gb46750(100.0, 9000.0) == 18200)


# ── Test 3: Unique Product ID ────────────────────────────────────────────

section("Test 3: 唯一产品识别码 (Item 001)")

serial = b"PROD1234567890123456"
encoded = _encode_unique_product_id(serial)
check("长度 = 20 字节", len(encoded) == 20, f"got {len(encoded)}")
check("内容匹配 (20 字节完整)", encoded == serial.ljust(20, b'\x00'))

# Short serial
short_serial = b"SHORT"
encoded2 = _encode_unique_product_id(short_serial)
check("短序列号 NULL 填充", encoded2[:5] == b"SHORT" and encoded2[5:20] == b'\x00' * 15)


# ── Test 4: Registration Mark ────────────────────────────────────────────

section("Test 4: 实名登记标志 (Item 002)")

mark = "UAS12345"
encoded = _encode_registration_mark(mark)
check("长度 = 8 字节", len(encoded) == 8, f"got {len(encoded)}")
check("内容匹配 (左对齐 NULL 填充)", encoded[:7] == b"UAS1234" and encoded[7] == 0x35)

# Short mark (pads with NULL)
short_mark = "UAS12"
encoded2 = _encode_registration_mark(short_mark)
check("短标志 NULL 填充", encoded2[5:8] == b'\x00' * 3)


# ── Test 5: Operation Category & UA Classification ───────────────────────

section("Test 5: 运行类别与UA分类 (Items 003, 004)")

check("开放类 (1)", _encode_operation_category(1) == b'\x01')
check("特定类 (2)", _encode_operation_category(2) == b'\x02')
check("审定类 (3)", _encode_operation_category(3) == b'\x03')

check("微型 (0)", _encode_ua_classification(0) == b'\x00')
check("轻型 (1)", _encode_ua_classification(1) == b'\x01')
check("小型 (2)", _encode_ua_classification(2) == b'\x02')
check("中型 (3)", _encode_ua_classification(3) == b'\x03')
check("大型 (4)", _encode_ua_classification(4) == b'\x04')


# ── Test 6: Station Location ─────────────────────────────────────────────

section("Test 6: 遥控站位置与高度 (Items 005, 006, 007)")

check("起飞点 (0)", _encode_station_location_type(0) == b'\x00')
check("遥控站 (1)", _encode_station_location_type(1) == b'\x01')

# Station location: lat/lng ×1e7 LE
lat, lng = 399267000, 1163830000
encoded_loc = _encode_station_location(lat, lng)
check("长度 = 8 字节", len(encoded_loc) == 8)
check("Lat = 399267000 (LE)", struct.unpack('<i', encoded_loc[0:4])[0] == lat)
check("Lng = 1163830000 (LE)", struct.unpack('<i', encoded_loc[4:8])[0] == lng)

# Unknown location
encoded_unk = _encode_station_location(None, None)
check("未知位置 = 0xFFFFFFFF", struct.unpack('<I', encoded_unk[0:4])[0] == 0xFFFFFFFF)

# Station altitude: (25+1000)×2 = 2050
encoded_alt = _encode_station_altitude(25.0)
check("25m → 2050", struct.unpack('<H', encoded_alt)[0] == 2050)


# ── Test 7: UA Position ──────────────────────────────────────────────────

section("Test 7: UA位置 (Item 008)")

encoded = _encode_ua_position(399267000, 1163830000)
check("长度 = 8 字节", len(encoded) == 8)
check("Lat LE", struct.unpack('<i', encoded[0:4])[0] == 399267000)
check("Lng LE", struct.unpack('<i', encoded[4:8])[0] == 1163830000)


# ── Test 8: Track Angle & Ground Speed ───────────────────────────────────

section("Test 8: 航迹角与地速 (Items 009, 010)")

# Track angle: 90.5° → 905
encoded_angle = _encode_track_angle(90.5)
check("90.5° → 905", struct.unpack('<H', encoded_angle)[0] == 905)

# Track angle: 359.9° → 3599
encoded_angle2 = _encode_track_angle(359.9)
check("359.9° → 3599", struct.unpack('<H', encoded_angle2)[0] == 3599)

# Unknown angle → 0xFFFF
encoded_angle3 = _encode_track_angle(None)
check("未知角度 → 0xFFFF", struct.unpack('<H', encoded_angle3)[0] == 0xFFFF)

# Ground speed: 15.5 m/s → 155
encoded_spd = _encode_ground_speed(15.5)
check("15.5m/s → 155", struct.unpack('<H', encoded_spd)[0] == 155)

# Unknown speed → 0xFFFF
encoded_spd2 = _encode_ground_speed(None)
check("未知速度 → 0xFFFF", struct.unpack('<H', encoded_spd2)[0] == 0xFFFF)


# ── Test 9: Relative Height ──────────────────────────────────────────────

section("Test 9: 相对高度 (Item 011)")

# 100m → (100+9000)×2 = 18200
encoded = _encode_relative_height(100.0)
check("100m → 18200", struct.unpack('<H', encoded)[0] == 18200)

# 0m → (0+9000)×2 = 18000
encoded2 = _encode_relative_height(0.0)
check("0m → 18000", struct.unpack('<H', encoded2)[0] == 18000)


# ── Test 10: Vertical Speed ──────────────────────────────────────────────

section("Test 10: 垂直速度 (Item 012)")

# 5.0 m/s up → bit7=0, val=10
encoded = _encode_vertical_speed(5.0)
check("5.0m/s↑ bit7=0", (encoded[0] & 0x80) == 0)
check("5.0m/s↑ val=10", (encoded[0] & 0x7F) == 10)

# 3.0 m/s down → bit7=1, val=6
encoded2 = _encode_vertical_speed(-3.0)
check("3.0m/s↓ bit7=1", (encoded2[0] & 0x80) != 0)
check("3.0m/s↓ val=6", (encoded2[0] & 0x7F) == 6)

# Unknown → 0xFF
encoded3 = _encode_vertical_speed(None)
check("未知 → 0xFF", encoded3[0] == 0xFF)


# ── Test 11: Altitudes ───────────────────────────────────────────────────

section("Test 11: 大地高度与气压高度 (Items 013, 014)")

# Geodetic: 125m → (125+1000)×2 = 2250
encoded_geo = _encode_geodetic_altitude(125.0)
check("大地高度 125m → 2250", struct.unpack('<H', encoded_geo)[0] == 2250)

# Barometric: 120m → (120+1000)×2 = 2240
encoded_baro = _encode_barometric_altitude(120.0)
check("气压高度 120m → 2240", struct.unpack('<H', encoded_baro)[0] == 2240)


# ── Test 12: Operation Status & Coordinate System ────────────────────────

section("Test 12: 运行状态与坐标系 (Items 015, 016)")

check("未报告 (0)", _encode_operation_status(0) == b'\x00')
check("地面 (1)", _encode_operation_status(1) == b'\x01')
check("空中 (2)", _encode_operation_status(2) == b'\x02')
check("紧急状态 (3)", _encode_operation_status(3) == b'\x03')

check("WGS-84 (0)", _encode_coordinate_system(0) == b'\x00')
check("CGCS2000 (1)", _encode_coordinate_system(1) == b'\x01')


# ── Test 13: Accuracy ────────────────────────────────────────────────────

section("Test 13: 精度项 (Items 017, 018, 019)")

check("水平精度 9 (<30m)", _encode_horizontal_accuracy(9) == b'\x09')
check("垂直精度 4 (<10m)", _encode_vertical_accuracy(4) == b'\x04')
check("速度精度 2 (<3m/s)", _encode_speed_accuracy(2) == b'\x02')


# ── Test 14: Timestamp ───────────────────────────────────────────────────

section("Test 14: 时间戳 (Items 020, 021)")

ts_ms = 1717300000000  # 2024-06-02
encoded = _encode_timestamp(ts_ms)
check("长度 = 6 字节", len(encoded) == 6, f"got {len(encoded)}")
decoded_ts = _decode_timestamp_6le(encoded)
check("时间戳 round-trip", decoded_ts == ts_ms, f"{decoded_ts} vs {ts_ms}")

# Current time
encoded_now = _encode_timestamp()
check("当前时间戳 6 字节", len(encoded_now) == 6)

# Timestamp accuracy
check("≤0.1s (5)", _encode_timestamp_accuracy(5) == b'\x05')


# ── Test 15: Full Packet Build ───────────────────────────────────────────

section("Test 15: 完整 GB 46750 数据包组装")

packet = build_gb46750_packet(
    serial=b"PROD1234567890123456",
    registration_mark="UAS12345678",
    operation_category=OperationCategory.OPEN,
    ua_classification=UAClassification.LIGHT,
    station_location_type=StationLocationType.TAKEOFF,
    station_lat=399267000,
    station_lng=1163830000,
    station_altitude=25.0,
    ua_lat=399268000,
    ua_lng=1163840000,
    track_angle=90.0,
    ground_speed=15.0,
    relative_height=100.0,
    vertical_speed=2.5,
    geodetic_altitude=125.0,
    barometric_altitude=120.0,
    operation_status=OperationStatus.AIR,
    coordinate_system=CoordinateSystem.WGS84,
    horizontal_accuracy=9,
    vertical_accuracy=4,
    speed_accuracy=2,
    timestamp_ms=1717300000000,
    timestamp_accuracy=5,
    include_optional=True,
)

check("包非空", len(packet) > 0, f"总长度 {len(packet)}")

# Header: DataType(1) + Version(1) + Length(1)
check("DataType = 0xFF", packet[0] == 0xFF)
version = packet[1]
check("Version bits 7-5 = 001", (version & 0xE0) == 0x20, f"got 0x{version:02X}")
check("Version V1.0 = 0x20", version == 0x20, f"got 0x{version:02X}")

data_len = packet[2]
check("DataLength > 0", data_len > 0, f"got {data_len}")

# Verify total expected data size
expected_data_size = sum(ITEM_SIZES[i] for i in sorted(MANDATORY_ITEMS | OPTIONAL_ITEMS))
check(f"DataLength = {expected_data_size} (all 21 items)", data_len == expected_data_size,
      f"got {data_len}")


# ── Test 16: Round-trip Decode ───────────────────────────────────────────

section("Test 16: 完整编解码 Round-trip")

decoded = decode_gb46750_packet(packet)
check("解码成功", decoded is not None)
check("类型 = GB46750-2025", decoded.get("_type") == "GB46750-2025")

check("唯一产品识别码 = 'PROD1234567890123456'",
      decoded.get("唯一产品识别码") == "PROD1234567890123456",
      f"got '{decoded.get('唯一产品识别码', '')}'")

check("实名登记标志 = '12345678' (取后8位)",
      decoded.get("实名登记标志") == "12345678",
      f"got '{decoded.get('实名登记标志', '')}'")

check("运行类别 = 开放类",
      decoded.get("运行类别") == "开放类",
      f"got '{decoded.get('运行类别', '')}'")

check("UA分类 = 轻型",
      decoded.get("UA分类") == "轻型",
      f"got '{decoded.get('UA分类', '')}'")

check("运行状态 = 空中",
      decoded.get("运行状态") == "空中",
      f"got '{decoded.get('运行状态', '')}'")

check("坐标系类型 = WGS-84",
      decoded.get("坐标系类型") == "WGS-84",
      f"got '{decoded.get('坐标系类型', '')}'")

# Check UA position
ua_pos = decoded.get("UA位置", "")
check("UA位置包含 39.9268", "39.926800" in ua_pos, ua_pos)
check("UA位置包含 116.384", "116.384000" in ua_pos)

# Check track angle
check("航迹角 = 90.0°", "90.0°" in decoded.get("航迹角", ""),
      decoded.get("航迹角", ""))

# Check ground speed
check("地速 = 15.0m/s", "15.0m/s" in decoded.get("地速", ""),
      decoded.get("地速", ""))

# Check vertical speed
vs = decoded.get("垂直速度", "")
check("垂直速度 = 2.5m/s ↑", "2.5m/s" in vs and "↑" in vs, vs)

# Check timestamp
check("时间戳包含 2024", "2024" in decoded.get("时间戳", ""),
      decoded.get("时间戳", ""))


# ── Test 17: Without Optional Items ──────────────────────────────────────

section("Test 17: 仅必选项 (无可选)")

packet_man = build_gb46750_packet(
    serial=b"PROD1234567890123456",
    registration_mark="UAS12345678",
    ua_classification=UAClassification.MICRO,
    station_location_type=StationLocationType.TAKEOFF,
    station_lat=399267000,
    station_lng=1163830000,
    station_altitude=25.0,
    ua_lat=399268000,
    ua_lng=1163840000,
    track_angle=90.0,
    ground_speed=15.0,
    geodetic_altitude=125.0,
    operation_status=OperationStatus.AIR,
    coordinate_system=CoordinateSystem.WGS84,
    horizontal_accuracy=9,
    vertical_accuracy=4,
    speed_accuracy=2,
    timestamp_accuracy=5,
    include_optional=False,
)

check("必选项包非空", len(packet_man) > 0)
expected_mandatory_size = sum(ITEM_SIZES[i] for i in sorted(MANDATORY_ITEMS))
check(f"DataLength = {expected_mandatory_size} (only mandatory)",
      packet_man[2] == expected_mandatory_size,
      f"got {packet_man[2]}")

decoded_man = decode_gb46750_packet(packet_man)
check("解码成功", decoded_man is not None)
check("不包含可选字段 运行类别", "运行类别" not in decoded_man)


# ── Test 18: Decode Invalid Packet ───────────────────────────────────────

section("Test 18: 无效数据包解码")

check("空数据 → None", decode_gb46750_packet(b'') is None)
check("短数据 → None", decode_gb46750_packet(b'\xFF\x01') is None)
check("错误 DataType → None", decode_gb46750_packet(b'\x00\x01\x00') is None)


# ── Test 19: Item Sizes Consistency ──────────────────────────────────────

section("Test 19: 数据项尺寸一致性")

for item_id in range(1, 22):
    has_size = item_id in ITEM_SIZES
    check(f"Item {item_id:03d} 有定义尺寸", has_size,
          f"size={ITEM_SIZES.get(item_id, 'MISSING')}")


# ── Test 20: DroneState GB 46750 Fields ──────────────────────────────────

section("Test 20: DroneState GB 46750 字段")

drone = DroneState(
    serial=b"PROD1234567890123456",
    pilot_location=(399267000, 1163830000),
    lat=399268000,
    lng=1163840000,
    mac_address="02:00:00:00:00:01",
    ble_address="00:00:00:00:00:01",
    registration_mark="UAS12345678",
    operation_category=OperationCategory.OPEN,
    ua_classification=UAClassification.LIGHT,
    station_location_type=StationLocationType.TAKEOFF,
    horizontal_accuracy=9,
    vertical_accuracy=4,
    speed_accuracy=2,
    timestamp_accuracy=5,
    speed=15.0,
    geodetic_altitude=125.0,
    pressure_altitude=120.0,
    height=100.0,
    direction=90.0,
    vertical_speed=2.5,
    operator_altitude=25.0,
)

check("registration_mark", drone.registration_mark == "UAS12345678")
check("operation_category = 1 (开放类)", drone.operation_category == 1)
check("ua_classification = 1 (轻型)", drone.ua_classification == 1)
check("station_location_type = 0 (起飞点)", drone.station_location_type == 0)
check("horizontal_accuracy = 9", drone.horizontal_accuracy == 9)
check("vertical_accuracy = 4", drone.vertical_accuracy == 4)
check("speed_accuracy = 2", drone.speed_accuracy == 2)
check("timestamp_accuracy = 5", drone.timestamp_accuracy == 5)


# ── Test 21: GB 46750 Beacon Frame Construction ──────────────────────────

section("Test 21: GB 46750 Beacon 帧构造验证")

try:
    from scapy.all import RadioTap, Dot11, Dot11Beacon, Dot11Elt

    OUI = b'\xfa\x0b\xbc'
    VEND_TYPE = 0x0E

    # Build GB 46750 packet
    gb_packet = build_gb46750_packet(
        serial=b"PROD1234567890123456",
        registration_mark="UAS12345678",
        ua_classification=UAClassification.LIGHT,
        station_location_type=StationLocationType.TAKEOFF,
        station_lat=399267000,
        station_lng=1163830000,
        station_altitude=25.0,
        ua_lat=399268000,
        ua_lng=1163840000,
        track_angle=90.0,
        ground_speed=15.0,
        geodetic_altitude=125.0,
        operation_status=OperationStatus.AIR,
        coordinate_system=CoordinateSystem.WGS84,
        horizontal_accuracy=9,
        vertical_accuracy=4,
        speed_accuracy=2,
        timestamp_accuracy=5,
    )

    # Vendor IE data: VendType(0x0E) + MessageCounter(0) + GB46750Packet
    vendor_data = bytes([VEND_TYPE, 0]) + gb_packet

    ssid = "GB47-PROD12345678901234"[:32]

    ie_ssid = Dot11Elt(ID='SSID', info=ssid.encode())
    ie_rates = Dot11Elt(ID='Rates', info=b'\x82\x84\x8b\x96')
    ie_dsset = Dot11Elt(ID='DSset', info=bytes([6]))
    ie_tim = Dot11Elt(ID='TIM', info=b'\x00\x01\x00\x00')
    ie_erp = Dot11Elt(ID='ERPinfo', info=b'\x00')
    ie_esr = Dot11Elt(ID='ESRates', info=b'\x0c\x12\x18\x24\x30\x48\x60\x6c')
    ie_vendor = Dot11Elt(ID=221, info=OUI + vendor_data)

    radiotap = RadioTap()
    dot11_base = Dot11(type=0, subtype=8,
                       addr1='ff:ff:ff:ff:ff:ff',
                       addr2='02:00:00:00:00:01',
                       addr3='02:00:00:00:00:01', SC=0)
    beacon_base = Dot11Beacon(cap=0, timestamp=0)

    frame = radiotap / dot11_base / beacon_base / ie_ssid / ie_rates / ie_dsset / ie_tim / ie_erp / ie_esr / ie_vendor
    raw = bytes(frame)

    check("Beacon 帧构造成功", len(raw) > 0, f"帧长度 {len(raw)}")

    # Verify 802.11 Header
    rt_len = raw[2] | (raw[3] << 8)
    mgmt = raw[rt_len:rt_len+24]
    fc = struct.unpack('<H', mgmt[0:2])[0]
    check("Frame Control = 0x0080 (Beacon)", fc == 0x0080, f"got 0x{fc:04x}")

    da = mgmt[4:10]
    check("DA = broadcast", da == b'\xff' * 6)

    # Find Vendor IE 221
    ie_off = rt_len + 24 + 12
    found_vendor = False
    off = ie_off
    while off < len(raw):
        ie_id = raw[off]
        ie_len = raw[off + 1]
        ie_data = raw[off + 2:off + 2 + ie_len]
        if ie_id == 221:
            found_vendor = True
            check("OUI = FA:0B:BC", ie_data[:3] == b'\xfa\x0b\xbc',
                  f"got {ie_data[:3].hex(':')}")
            check("VendType = 0x0E (GB 46750)", ie_data[3] == 0x0E,
                  f"got 0x{ie_data[3]:02X}")
            check("MessageCounter = 0", ie_data[4] == 0)
            # Decode the GB 46750 packet
            decoded_in_frame = decode_gb46750_packet(ie_data[5:])
            check("帧中 GB 46750 数据包解码成功", decoded_in_frame is not None)
            if decoded_in_frame:
                check("帧中唯一产品识别码正确",
                      "PROD1234567890123456" in decoded_in_frame.get("唯一产品识别码", ""))
            break
        off += 2 + ie_len

    check("Vendor IE (221) 存在于帧中", found_vendor)

except ImportError:
    print("  ⚠️  Scapy 未安装，跳过 Beacon 帧构造验证")
    check("Scapy 可用", False, "安装: pip install scapy")


# ── Summary ──────────────────────────────────────────────────────────────

summary()
