#!/usr/bin/env python3
"""GB 42590-2023 完整消息格式验证脚本。

逐项验证 GB 42590 传输中所有 5 种 ASTM F3411 消息类型的编码/解码正确性：
  - Basic ID (0x00): 序列号、ID类型、UA类型
  - Location (0x01): 经纬度、高度、速度、方向、时间戳
  - Self ID (0x03): 描述文本
  - System (0x04): 飞手位置(经纬度+高度)、飞行区域
  - Operator ID (0x05): 操作员注册号(CAA风格)

同时验证 GB Message Pack 格式、协议版本、counter 机制等。

Usage:
    python3 verify_gb_messages.py              # 运行所有测试
    python3 verify_gb_messages.py --verbose    # 详细输出
"""

import argparse
import struct
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from drone_rid_spoofer.messages import (
    encode_basic_id, decode_basic_id,
    encode_location, decode_location,
    encode_self_id, decode_self_id,
    encode_system, decode_system,
    encode_operator_id, decode_operator_id,
    build_gb_pack, build_message_pack, decode_message_pack,
    MESSAGE_SIZE, MsgType,
    IDTYPE_SERIAL_NUMBER, IDTYPE_CAA_REGISTRATION,
    UATYPE_HELICOPTER, UATYPE_AEROPLANE,
    STATUS_AIRBORNE, STATUS_GROUND,
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


# ── Test 1: Basic ID message ─────────────────────────────────────────────

section("Test 1: Basic ID (MessageType=0x00)")

serial = b"GB_TEST_00001"
msg = encode_basic_id(serial, proto=1,
                      id_type=IDTYPE_SERIAL_NUMBER,
                      ua_type=UATYPE_HELICOPTER)

check("消息长度 25 字节", len(msg) == 25, f"got {len(msg)}")
check("MsgType = 0x0 (高4位)", msg[0] >> 4 == 0x0, f"got 0x{msg[0]>>4:X}")
check("Proto = 1 (低4位)", msg[0] & 0x0F == 1, f"got {msg[0] & 0x0F}")
check("UAType = HELICOPTER/Multirotor (0x02) 在低4位", (msg[1] & 0x0F) == 0x02, f"got {msg[1] & 0x0F}")
check("IDType = SERIAL_NUMBER (0x01) 在高4位", (msg[1] >> 4) == 0x01, f"got {msg[1] >> 4}")
check("UASID 前14字节 = 'GB_TEST_00001'", msg[2:15] == serial, f"got '{msg[2:15].decode()}'")
check("UASID 第15-21字节补零", msg[15:22] == b'\x00' * 7)

# 测试 CAA Registration ID type
msg2 = encode_basic_id(b"CAA_REG_12345", proto=1,
                       id_type=IDTYPE_CAA_REGISTRATION,
                       ua_type=UATYPE_AEROPLANE)
check("CAA ID Type (0x02) 在高4位", (msg2[1] >> 4) == 0x02, f"got {msg2[1] >> 4}")
check("UAType = AEROPLANE (0x01) 在低4位", (msg2[1] & 0x0F) == 0x01, f"got {msg2[1] & 0x0F}")

# 测试序列号超长截断
long_serial = b"A" * 30
msg3 = encode_basic_id(long_serial, proto=1)
check("长序列号截断为20字节", msg3[2:22] == b'A' * 20)

# 测试 round-trip decode
decoded = decode_basic_id(msg)
check("decode: 包含 'GB_TEST_00001'", "GB_TEST_00001" in decoded.get("Basic ID", ""),
      decoded.get("Basic ID", ""))
check("decode: 包含 'ID=Serial'", "ID=Serial" in decoded.get("Basic ID", ""))
check("decode: 包含 'UA=Helicopter/Multirotor'", "UA=Helicopter/Multirotor" in decoded.get("Basic ID", ""))


# ── Test 2: Location message ─────────────────────────────────────────────

section("Test 2: Location (MessageType=0x01)")

drone = DroneState(
    serial=b"LOC_TEST_01",
    pilot_location=(399267000, 1163830000),
    lat=399267000,  # 39.9267°
    lng=1163830000,  # 116.383°
    mac_address="02:00:00:00:01:01",
    ble_address="00:00:00:00:01:01",
    direction=90,
    speed=15.0,
    vertical_speed=2.5,
    pressure_altitude=120.0,
    geodetic_altitude=125.0,
    height=100.0,
)

msg = encode_location(drone, proto=1, status=STATUS_AIRBORNE)

check("消息长度 25 字节", len(msg) == 25, f"got {len(msg)}")
check("MsgType = 0x1 (高4位)", msg[0] >> 4 == 0x1, f"got 0x{msg[0]>>4:X}")
check("Proto = 1 (低4位)", msg[0] & 0x0F == 1, f"got {msg[0] & 0x0F}")

# Status (bits 7-4)
status = (msg[1] >> 4) & 0x0F
check("Status = AIRBORNE (0x02)", status == STATUS_AIRBORNE, f"got {status}")

# EW direction bit (bit 1)
ew_dir = (msg[1] >> 1) & 0x01
check("EW Direction = 0 (东经, 方向90°<180)", ew_dir == 0, f"got {ew_dir}")

# Direction byte
check("Direction byte = 90", msg[2] == 90, f"got {msg[2]}")

# Speed horizontal: 15.0 / 0.25 = 60
check("SpeedH = 60 (15.0 m/s ÷ 0.25)", msg[3] == 60, f"got {msg[3]}")

# Speed vertical: 2.5 / 0.5 = 5
check("SpeedV = 5 (2.5 m/s ÷ 0.5)", struct.unpack("<b", msg[4:5])[0] == 5,
      f"got {struct.unpack('<b', msg[4:5])[0]}")

# Latitude: int32 LE ×1e7
lat_decoded = struct.unpack("<i", msg[5:9])[0]
check("Latitude = 399267000 (39.9267°)", lat_decoded == 399267000, f"got {lat_decoded}")

# Longitude: int32 LE ×1e7
lng_decoded = struct.unpack("<i", msg[9:13])[0]
check("Longitude = 1163830000 (116.383°)", lng_decoded == 1163830000, f"got {lng_decoded}")

# Altitude Barometric: (120 + 1000) / 0.5 = 2240
baro = struct.unpack("<H", msg[13:15])[0]
check("AltBaro = 2240 (120.0m)", baro == 2240, f"got {baro}")

# Altitude Geodetic: (125 + 1000) / 0.5 = 2250
geo = struct.unpack("<H", msg[15:17])[0]
check("AltGeo = 2250 (125.0m)", geo == 2250, f"got {geo}")

# Height: (100 + 1000) / 0.5 = 2200
hgt = struct.unpack("<H", msg[17:19])[0]
check("Height = 2200 (100.0m)", hgt == 2200, f"got {hgt}")

# 测试 altitude 边界值
drone2 = DroneState(
    serial=b"BOUNDARY", pilot_location=(0, 0), lat=0, lng=0,
    mac_address="02:00:00:00:00:02", ble_address="00:00:00:00:00:02",
    pressure_altitude=-500.0, geodetic_altitude=8000.0, height=0.0,
)
msg_boundary = encode_location(drone2, proto=1)
# -500m → (-500+1000)/0.5 = 1000, clamped to 0..65535
baro_boundary = struct.unpack("<H", msg_boundary[13:15])[0]
check("负高度编码: -500m → 1000", baro_boundary == 1000, f"got {baro_boundary}")
# 8000m → (8000+1000)/0.5 = 18000, 未超过65535不会被clamp
geo_boundary = struct.unpack("<H", msg_boundary[15:17])[0]
check("超高编码: 8000m → 18000", geo_boundary == 18000, f"got {geo_boundary}")

# Round-trip decode
decoded = decode_location(msg)
check("decode: Status=Airborne", decoded["Status"] == "Airborne",
      decoded.get("Status", ""))
check("decode: Direction=90", decoded["Direction"] == 90,
      str(decoded.get("Direction", "")))
check("decode: SpeedH=15.00 m/s", "15.00" in decoded.get("Speed Horizontal", ""))
check("decode: SpeedV=2.50 m/s", "2.50" in decoded.get("Speed Vertical", ""))
check("decode: Latitude=39.9267", abs(decoded["Latitude"] - 39.9267) < 0.0001,
      f"{decoded['Latitude']:.6f}")
check("decode: Longitude=116.383", abs(decoded["Longitude"] - 116.383) < 0.0001,
      f"{decoded['Longitude']:.6f}")
check("decode: AltBaro=120.0m", "120.0" in decoded.get("Altitude Baro", ""),
      decoded.get("Altitude Baro", ""))
check("decode: AltGeo=125.0m", "125.0" in decoded.get("Altitude Geo", ""),
      decoded.get("Altitude Geo", ""))
check("decode: Height=100.0m above takeoff",
      "100.0" in decoded.get("Height", "") and "takeoff" in decoded.get("Height", ""),
      decoded.get("Height", ""))

# ── Test 3: Self ID message ──────────────────────────────────────────────

section("Test 3: Self ID (MessageType=0x03)")

msg = encode_self_id(b"GB Spoofer Test", proto=2, desc_type=0)

check("消息长度 25 字节", len(msg) == 25, f"got {len(msg)}")
check("MsgType = 0x3 (高4位)", msg[0] >> 4 == 0x3, f"got 0x{msg[0]>>4:X}")
check("Proto = 2 (低4位)", msg[0] & 0x0F == 2, f"got {msg[0] & 0x0F}")
check("DescType = 0 (Text)", msg[1] == 0, f"got {msg[1]}")
check("描述文本 = 'GB Spoofer Test' (前15字节)", msg[2:17] == b"GB Spoofer Test", f"got '{msg[2:17].decode()}'")
check("第16-25字节补零", msg[17:25] == b'\x00' * 8)

# 测试长文本截断 (max 23 bytes)
long_desc = b"A" * 30
msg2 = encode_self_id(long_desc, proto=2)
check("长描述截断为23字节", msg2[2:25] == b'A' * 23)

# 测试默认描述
msg3 = encode_self_id(proto=2)
check("默认描述 = 'GB Spoofer'", msg3[2:12] == b"GB Spoofer")

# Round-trip decode
decoded = decode_self_id(msg)
check("decode: 包含 'GB Spoofer Test'", "GB Spoofer Test" in decoded.get("Self-ID", ""))
check("decode: type=Text", "type=Text" in decoded.get("Self-ID", ""))


# ── Test 4: System message ───────────────────────────────────────────────

section("Test 4: System (MessageType=0x04)")

pilot_lat = 399267000   # 39.9267°
pilot_lng = 1163830000  # 116.383°
operator_alt = 25.0

msg = encode_system(pilot_lat, pilot_lng, proto=1,
                    operator_altitude=operator_alt)

check("消息长度 25 字节", len(msg) == 25, f"got {len(msg)}")
check("MsgType = 0x4 (高4位)", msg[0] >> 4 == 0x4, f"got 0x{msg[0]>>4:X}")
check("Proto = 1 (低4位)", msg[0] & 0x0F == 1, f"got {msg[0] & 0x0F}")

# Operator location type (byte 1)
check("Operator Location Type = 0x05 (live GNSS, airborne)", msg[1] == 0x05,
      f"got 0x{msg[1]:02X}")

# Operator latitude: int32 LE ×1e7
op_lat_decoded = struct.unpack("<i", msg[2:6])[0]
check("Operator Lat = 399267000 (39.9267°)", op_lat_decoded == 399267000,
      f"got {op_lat_decoded}")

# Operator longitude: int32 LE ×1e7
op_lng_decoded = struct.unpack("<i", msg[6:10])[0]
check("Operator Lng = 1163830000 (116.383°)", op_lng_decoded == 1163830000,
      f"got {op_lng_decoded}")

# Area fields (bytes 10-17): AreaCount, AreaRadius, AreaCeiling, AreaFloor
# 默认全为 0
area_count = struct.unpack("<H", msg[10:12])[0]
check("AreaCount = 0 (默认)", area_count == 0, f"got {area_count}")

area_radius = msg[12]
check("AreaRadius = 0 (默认)", area_radius == 0, f"got {area_radius}")

# Classification (byte 16)
check("Classification = 0x12 (EU category specific)", msg[16] == 0x12,
      f"got 0x{msg[16]:02X}")

# Operator altitude: (25 + 1000) / 0.5 = 2050
op_alt_raw = struct.unpack("<H", msg[18:20])[0]
check("Operator Altitude = 2050 (25.0m)", op_alt_raw == 2050, f"got {op_alt_raw}")

# 测试 operator altitude = 0
msg2 = encode_system(pilot_lat, pilot_lng, proto=1, operator_altitude=0.0)
op_alt_zero = struct.unpack("<H", msg2[18:20])[0]
check("Operator Altitude 0m = 2000", op_alt_zero == 2000, f"got {op_alt_zero}")

# 测试 operator altitude = 500m
msg3 = encode_system(pilot_lat, pilot_lng, proto=1, operator_altitude=500.0)
op_alt_500 = struct.unpack("<H", msg3[18:20])[0]
check("Operator Altitude 500m = 3000", op_alt_500 == 3000, f"got {op_alt_500}")

# Round-trip decode
decoded = decode_system(msg)
check("decode: op=(39.9267, 116.383, 25.0m)",
      "39.926700" in decoded.get("System", ""),
      decoded.get("System", ""))
check("decode: 包含 116.383",
      "116.383000" in decoded.get("System", ""))
check("decode: 包含 25.0m (operator altitude)",
      "25.0m" in decoded.get("System", ""))


# ── Test 5: Operator ID message ──────────────────────────────────────────

section("Test 5: Operator ID (MessageType=0x05)")

op_id = "OP-12345"
msg = encode_operator_id(operator_id=op_id, proto=2)

check("消息长度 25 字节", len(msg) == 25, f"got {len(msg)}")
check("MsgType = 0x5 (高4位)", msg[0] >> 4 == 0x5, f"got 0x{msg[0]>>4:X}")
check("Proto = 2 (低4位)", msg[0] & 0x0F == 2, f"got {msg[0] & 0x0F}")
check("OperatorIdType = 0 (CAA registration)", msg[1] == 0, f"got {msg[1]}")

# Operator ID bytes 2-21 (20 bytes max)
op_id_bytes = msg[2:22]
check("Operator ID 'OP-12345' (前8字节)", op_id_bytes[:8] == b"OP-12345",
      f"got '{op_id_bytes[:8].decode()}'")
check("剩余12字节补零", op_id_bytes[8:] == b'\x00' * 12)

# 测试 CAA 格式
caa_id = "GBR-OP-1234567890"
msg2 = encode_operator_id(operator_id=caa_id, proto=2)
caa_encoded = caa_id.encode().ljust(20, b'\x00')
check(f"CAA ID '{caa_id}' ({len(caa_id)}字节, left-justified)", msg2[2:22] == caa_encoded,
      f"got '{msg2[2:22].decode('utf-8', errors='replace')}'")

# 测试超长截断
long_id = "THIS_IS_A_VERY_LONG_OPERATOR_ID_EXCEEDING_20_BYTES"
msg3 = encode_operator_id(operator_id=long_id, proto=2)
check("长ID截断为20字节", msg3[2:22] == long_id.encode()[:20],
      f"got '{msg3[2:22].decode()}'")

# 测试空字符串
msg4 = encode_operator_id(operator_id="", proto=2)
check("空ID → 全零填充", msg4[2:22] == b'\x00' * 20)

# 测试中文 UTF-8
cn_id = "飞手-001"
msg5 = encode_operator_id(operator_id=cn_id, proto=2)
expected_cn = cn_id.encode('utf-8').ljust(20, b'\x00')
check(f"中文ID '{cn_id}' (UTF-8编码)", msg5[2:22] == expected_cn,
      f"got '{msg5[2:22].decode('utf-8', errors='replace')}'")

# Round-trip decode (decode_operator_id 现在包含 type 信息)
decoded = decode_operator_id(msg)
check("decode: Operator ID 包含 'OP-12345'", "OP-12345" in decoded.get("Operator ID", ""),
      f"got '{decoded.get('Operator ID', '')}'")
check("decode: Operator ID 包含 type=Operator ID",
      "type=Operator ID" in decoded.get("Operator ID", ""))

decoded2 = decode_operator_id(msg2)
check("decode: CAA ID round-trip", "GBR-OP-1234567890" in decoded2.get("Operator ID", ""))

decoded5 = decode_operator_id(msg5)
check("decode: 中文 ID round-trip", "飞手-001" in decoded5.get("Operator ID", ""))


# ── Test 6: GB Message Pack 完整组装 ─────────────────────────────────────

section("Test 6: GB Message Pack (完整组装)")

drone = DroneState(
    serial=b"GB_PACK_01",
    pilot_location=(399267000, 1163830000),
    lat=399267000, lng=1163830000,
    mac_address="02:00:00:00:01:01",
    ble_address="00:00:00:00:01:01",
    speed=10.0, vertical_speed=1.0,
    pressure_altitude=100.0, geodetic_altitude=105.0, height=50.0,
    operator_id="GB-OP-PACK-01",
    operator_altitude=20.0,
)

pack = build_gb_pack(drone, send_counter=42, proto=2)

# 总长度：VendType(1) + Counter(1) + PackHeader(3) + 5×25 = 2 + 3 + 125 = 130
check("GB Pack 总长度 130 字节", len(pack) == 130, f"got {len(pack)}")

# Vend Type (byte 0) = 0x0D
check("VendType = 0x0D", pack[0] == 0x0D, f"got 0x{pack[0]:02X}")

# Message Counter (byte 1)
check("MessageCounter = 42", pack[1] == 42, f"got {pack[1]}")

# Message Pack header (bytes 2-4)
check("Pack MsgType = 0xF", pack[2] >> 4 == 0xF, f"got 0x{pack[2]>>4:X}")
check("Pack Proto = 2", pack[2] & 0x0F == 2, f"got {pack[2] & 0x0F}")
check("MsgSize = 25", pack[3] == 25, f"got {pack[3]}")
check("MsgCount = 5", pack[4] == 5, f"got {pack[4]}")

# 逐条消息验证 (从 byte 5 开始)
msg_basic = pack[5:30]
check("Message#1: Basic ID 类型=0x0", msg_basic[0] >> 4 == 0x0, f"got 0x{msg_basic[0]>>4:X}")
check("Message#1: 包含 'GB_PACK_01'", b"GB_PACK_01" in msg_basic[2:22])

msg_loc = pack[30:55]
check("Message#2: Location 类型=0x1", msg_loc[0] >> 4 == 0x1, f"got 0x{msg_loc[0]>>4:X}")
loc_lat = struct.unpack("<i", msg_loc[5:9])[0]
check("Message#2: Latitude = 399267000", loc_lat == 399267000, f"got {loc_lat}")

msg_self = pack[55:80]
check("Message#3: Self ID 类型=0x3", msg_self[0] >> 4 == 0x3, f"got 0x{msg_self[0]>>4:X}")
check("Message#3: 描述 = 'GB42590 Drone Remote ID'", b"GB42590 Drone Remote ID" in msg_self[2:])

msg_sys = pack[80:105]
check("Message#4: System 类型=0x4", msg_sys[0] >> 4 == 0x4, f"got 0x{msg_sys[0]>>4:X}")
sys_op_lat = struct.unpack("<i", msg_sys[2:6])[0]
check("Message#4: Operator Lat = 399267000", sys_op_lat == 399267000, f"got {sys_op_lat}")
sys_op_alt = struct.unpack("<H", msg_sys[18:20])[0]
check("Message#4: Operator Alt = 2040 (20m)", sys_op_alt == 2040, f"got {sys_op_alt}")

msg_op = pack[105:130]
check("Message#5: Operator ID 类型=0x5", msg_op[0] >> 4 == 0x5, f"got 0x{msg_op[0]>>4:X}")
check("Message#5: OperatorIdType = 0", msg_op[1] == 0, f"got {msg_op[1]}")
check("Message#5: ID = 'GB-OP-PACK-01'", msg_op[2:15] == b"GB-OP-PACK-01")

# Round-trip decode: Message Pack body starts after VendType(1) + Counter(1) + PackHeader(3) = offset 5
fields = decode_message_pack(pack[5:], msg_count=5)
# decode_message_pack 展开所有子字段（Location 一条就有 11 个子字段），总字段数 > 5
check("decode: >= 5 个字段解码成功", len(fields) >= 5, f"got {len(fields)} fields: {list(fields.keys())}")
check("decode: Basic ID 存在", "Basic ID" in fields)
check("decode: Latitude 存在", "Latitude" in fields)
check("decode: Self-ID 存在", "Self-ID" in fields)
check("decode: System 存在", "System" in fields)
check("decode: Operator ID 存在", "Operator ID" in fields)
check("decode: Operator ID = 'GB-OP-PACK-01'",
      "GB-OP-PACK-01" in fields.get("Operator ID", ""),
      f"got '{fields.get('Operator ID', '')}'")


# ── Test 7: Counter 递增验证 ─────────────────────────────────────────────

section("Test 7: Counter 递增机制")

counters = []
for i in range(5):
    pack = build_gb_pack(drone, send_counter=i, proto=2)
    counters.append(pack[1])  # Message Counter is at byte 1 (after VendType)

check("Counter[0] = 0", counters[0] == 0)
check("Counter[1] = 1", counters[1] == 1)
check("Counter[2] = 2", counters[2] == 2)
check("Counter[3] = 3", counters[3] == 3)
check("Counter[4] = 4", counters[4] == 4)

# Counter 溢出回绕
pack_wrap = build_gb_pack(drone, send_counter=256, proto=2)
check("Counter 256 → 0 (mod 256)", pack_wrap[1] == 0, f"got {pack_wrap[1]}")


# ── Test 8: 协议版本区分 ─────────────────────────────────────────────────

section("Test 8: 协议版本区分 (Proto=1 vs Proto=2)")

msg_p1 = encode_basic_id(b"PROTO_TEST", proto=1)
msg_p2 = encode_basic_id(b"PROTO_TEST", proto=2)

check("Proto=1: UAType在低4位", (msg_p1[1] & 0x0F) == UATYPE_HELICOPTER)
check("Proto=2: UAType在高4位", (msg_p2[1] >> 4) == UATYPE_HELICOPTER)

# Location proto 差异
drone_p = DroneState(
    serial=b"P", pilot_location=(0, 0), lat=0, lng=0,
    mac_address="02:00:00:00:00:99", ble_address="00:00:00:00:00:99",
)
loc_p1 = encode_location(drone_p, proto=1, status=STATUS_AIRBORNE)
loc_p2 = encode_location(drone_p, proto=2, status=STATUS_AIRBORNE)

# Proto=1: status 在 bits 7-4
s1 = (loc_p1[1] >> 4) & 0x0F
# Proto=2: status 在 bits 3-0
s2 = loc_p2[1] & 0x0F
check("Proto=1: Status=2 (bits 7-4)", s1 == STATUS_AIRBORNE, f"got {s1}")
check("Proto=2: Status=2 (bits 3-0)", s2 == STATUS_AIRBORNE, f"got {s2}")


# ── Test 9: 场景配置文件加载验证 ──────────────────────────────────────────

section("Test 9: 场景配置文件验证")

import json
scenario_dir = os.path.join(os.path.dirname(__file__), "scenarios")

scenario_files = [
    "gb_single.json",
    "gb_swarm.json",
    "gb_full_test.json",
]

for fname in scenario_files:
    fpath = os.path.join(scenario_dir, fname)
    if not os.path.exists(fpath):
        check(f"{fname}: 文件存在", False, f"未找到 {fpath}")
        continue

    try:
        with open(fpath) as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        check(f"{fname}: JSON 格式正确", False, str(e))
        continue

    check(f"{fname}: JSON 格式正确", True)

    # 验证 global 配置
    g = config.get("global", {})
    check(f"{fname}: global.transport = 'gb'", g.get("transport") == "gb",
          f"got '{g.get('transport')}'")
    check(f"{fname}: global.gb 配置存在", "gb" in g)
    if "gb" in g:
        check(f"{fname}: gb.channel 是整数", isinstance(g["gb"].get("channel"), (int, float)))

    # 验证 drones 配置
    drones = config.get("drones", [])
    check(f"{fname}: 有 {len(drones)} 架无人机", len(drones) > 0, f"got {len(drones)}")

    for i, d in enumerate(drones):
        mode = d.get("mode", "random")
        check(f"{fname}: drone[{i}] mode='{mode}' 合法",
              mode in ("random", "static", "waypoints"),
              f"got '{mode}'")

        serial = d.get("serial", "")
        check(f"{fname}: drone[{i}] serial 存在", bool(serial))

        # 检查是否包含 operator_id
        has_op_id = "operator_id" in d
        has_op_alt = "operator_altitude" in d
        if not has_op_id:
            print(f"    ⚠️  {fname}: drone[{i}] 缺少 operator_id (将自动生成)")
        if not has_op_alt:
            print(f"    ⚠️  {fname}: drone[{i}] 缺少 operator_altitude (将自动生成)")

        # waypoints 模式验证
        if mode == "waypoints":
            wpts = d.get("waypoints", [])
            check(f"{fname}: drone[{i}] waypoints 非空", len(wpts) > 0,
                  f"got {len(wpts)} waypoints")


# ── Test 10: 完整的端到端 GB Beacon 帧构造验证 ────────────────────────────

section("Test 10: GB Beacon 帧构造验证")

try:
    from scapy.all import RadioTap, Dot11, Dot11Beacon, Dot11Elt

    OUI = b'\xfa\x0b\xbc'
    VEND_TYPE = 0x0D
    SUPPORTED_RATES = b'\x82\x84\x8b\x96'
    EXTENDED_SUPPORTED_RATES = b'\x0c\x12\x18\x24\x30\x48\x60\x6c'

    # 按照 GB42590Backend.send_messages() 的方式构造 vendor_data
    # 格式: VendType(1) + MessageCounter(1) + PackHeader(3) + Messages
    messages_list = [
        encode_basic_id(drone.serial, proto=2),
        encode_location(drone, proto=2),
        encode_self_id(b"GB42590 Drone Remote ID", proto=2),
        encode_system(drone.pilot_location[0], drone.pilot_location[1],
                      proto=2, operator_altitude=drone.operator_altitude),
        encode_operator_id(operator_id=drone.operator_id, proto=2),
    ]
    msg_count = len(messages_list)
    pack_header = bytes([(MsgType.PACK << 4) | 0x02, MESSAGE_SIZE, msg_count & 0xFF])
    vendor_data = bytes([VEND_TYPE, 0]) + pack_header + b''.join(messages_list)

    ssid = ("GB-" + drone.serial.decode('ascii', errors='replace'))[:32]

    ie_ssid = Dot11Elt(ID='SSID', info=ssid.encode())
    ie_rates = Dot11Elt(ID='Rates', info=SUPPORTED_RATES)
    ie_dsset = Dot11Elt(ID='DSset', info=bytes([6]))
    ie_tim = Dot11Elt(ID='TIM', info=b'\x00\x01\x00\x00')
    ie_erp = Dot11Elt(ID='ERPinfo', info=b'\x00')
    ie_esr = Dot11Elt(ID='ESRates', info=EXTENDED_SUPPORTED_RATES)
    ie_vendor = Dot11Elt(ID=221, info=OUI + vendor_data)

    radiotap = RadioTap()
    dot11_base = Dot11(type=0, subtype=8,
                       addr1='ff:ff:ff:ff:ff:ff',
                       addr2=drone.mac_address,
                       addr3=drone.mac_address, SC=0)
    beacon_base = Dot11Beacon(cap=0, timestamp=0)

    frame = radiotap / dot11_base / beacon_base / ie_ssid / ie_rates / ie_dsset / ie_tim / ie_erp / ie_esr / ie_vendor
    raw = bytes(frame)

    check("Beacon 帧构造成功", len(raw) > 0, f"帧长度 {len(raw)}")

    # 验证 RadioTap
    rt_len = raw[2] | (raw[3] << 8)
    check("RadioTap 长度 > 0", rt_len > 0, f"got {rt_len}")

    # 验证 802.11 Header
    mgmt = raw[rt_len:rt_len+24]
    fc = struct.unpack('<H', mgmt[0:2])[0]
    check("Frame Control = 0x0080 (Beacon)", fc == 0x0080, f"got 0x{fc:04x}")

    da = mgmt[4:10]
    check("DA = broadcast (ff:ff:ff:ff:ff:ff)", da == b'\xff' * 6)

    sa = mgmt[10:16]
    bssid = mgmt[16:22]
    check("SA == BSSID", sa == bssid)

    # 验证 Beacon Body
    beacon_off = rt_len + 24
    beacon_body = raw[beacon_off:beacon_off+12]
    bi = struct.unpack('<H', beacon_body[8:10])[0]
    check("Beacon Interval = 100 TU (default)", bi == 100, f"got {bi}")

    cap_val = struct.unpack('<H', beacon_body[10:12])[0]
    check("Capability = 0x0000 (no ESS)", cap_val == 0x0000, f"got 0x{cap_val:04X}")

    # 验证所有标准 IEs 存在
    ie_off = beacon_off + 12
    found_ies = {}
    off = ie_off
    while off < len(raw):
        ie_id = raw[off]
        ie_len = raw[off + 1]
        found_ies[ie_id] = raw[off + 2:off + 2 + ie_len]
        off += 2 + ie_len

    check("SSID IE (0) 存在", 0 in found_ies)
    check("Rates IE (1) 存在", 1 in found_ies)
    check("DSset IE (3) 存在", 3 in found_ies)
    check("TIM IE (5) 存在", 5 in found_ies)
    check("ERPinfo IE (42) 存在", 42 in found_ies)
    check("ESRates IE (50) 存在", 50 in found_ies)

    # 验证 IE 221 (Vendor Specific)
    check("Vendor IE (221) 存在", 221 in found_ies)
    if 221 in found_ies:
        oui_data = found_ies[221]
        check("OUI = FA:0B:BC", oui_data[:3] == b'\xfa\x0b\xbc',
              f"got {oui_data[:3].hex(':')}")
        check("VendType = 0x0D", oui_data[3] == 0x0D, f"got 0x{oui_data[3]:02X}")
        # Message Counter
        check("MessageCounter = 0", oui_data[4] == 0, f"got {oui_data[4]}")
        # Message Pack: PackType|Proto, MsgSize, MsgCount
        check("Pack MsgType = 0xF", oui_data[5] >> 4 == 0xF)
        check("Pack Proto = 2", oui_data[5] & 0x0F == 2)
        check("MsgSize = 25", oui_data[6] == 25, f"got {oui_data[6]}")
        check("MsgCount = 5", oui_data[7] == 5, f"got {oui_data[7]}")
        # 验证 messages 总长度
        messages_data = oui_data[8:]
        check("Messages 总长度 = 125 字节 (5×25)", len(messages_data) == 125, f"got {len(messages_data)}")

except ImportError:
    print("  ⚠️  Scapy 未安装，跳过 Beacon 帧构造验证")
    check("Scapy 可用", False, "安装: pip install scapy")

# ── Summary ──────────────────────────────────────────────────────────────

summary()
