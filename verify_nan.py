#!/usr/bin/env python3
"""End-to-end verification: build NAN SDF frame and decode it with the sniffer logic.

Validates the complete NAN data path:
  1. Build messages via messages.py
  2. Construct NAN SDF frame via nan.py
  3. Decode the SDF back via sniff_gb.py's NAN parser
  4. Verify round-trip integrity

Usage:
    python3 verify_nan.py

Requires no hardware — purely software validation of the encoding/decoding pipeline.
"""

import os
import struct
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drone_rid_spoofer.messages import (
    build_all_messages, build_message_pack, decode_message_pack,
    build_gb_pack,
    encode_basic_id, encode_location, encode_self_id, encode_system, encode_operator_id,
    MESSAGE_SIZE, MsgType,
)
from drone_rid_spoofer.state import DroneState
from drone_rid_spoofer.transport.nan import (
    _build_nan_sdf, _build_nan_sync_beacon, _generate_cluster_id,
    NAN_OUI, NAN_MULTICAST_ADDR,
)

# Import the NAN SDF parser from sniff_gb
from drone_rid_spoofer.sniff_gb import _parse_nan_sdf

# ── Test 1: Build messages → Message Pack → NAN SDF ─────────────────────

print("=" * 60)
print("Test 1: NAN SDF frame construction")

drone = DroneState(
    serial=b'NAN_TEST_00001',
    pilot_location=(399267000, 1163830000),
    lat=399267000, lng=1163830000,
    mac_address='02:00:00:00:01:01',
    ble_address='00:00:00:00:01:01',
    speed=15.0, vertical_speed=2.5,
    pressure_altitude=120.0, geodetic_altitude=125.0, height=100.0,
    operator_id="OP-12345",
    operator_altitude=25.0,
)

messages = build_all_messages(drone, proto=2)
print(f"  Built {len(messages)} messages")
for i, m in enumerate(messages):
    print(f"    [{i}] type=0x{(m[0]>>4):X} ver={m[0]&0xF} len={len(m)}")

message_pack = build_message_pack(messages, proto=2)
print(f"  Message Pack: {len(message_pack)} bytes")
print(f"    header: type=0x{(message_pack[0]>>4):X} msg_size={message_pack[1]} count={message_pack[2]}")

cluster_id = _generate_cluster_id()
sdf = _build_nan_sdf(drone.mac_address, message_pack, cluster_id, seq_num=42)
print(f"  NAN SDF: {len(sdf)} bytes total")
print(f"    Radiotap + 802.11 Action + Public Action + NAN attrs")

# ── Test 2: Parse NAN SDF back ───────────────────────────────────────────

print("\n" + "=" * 60)
print("Test 2: NAN SDF parsing (round-trip decode)")

# _parse_nan_sdf expects raw bytes + radiotap length + parsed header dict
rt_len = 8  # minimal radiotap header
# Build a minimal header dict matching what the sniffer would see
hdr = {
    'fc': 0x00D0, 'type': 0, 'subtype': 0x0D,
    'da': bytes.fromhex(NAN_MULTICAST_ADDR.replace(':', '')),
    'sa': bytes.fromhex(drone.mac_address.replace(':', '')),
    'bssid': bytes.fromhex(NAN_MULTICAST_ADDR.replace(':', '')),
    'seq_num': 42, 'frag_num': 0,
}

result = _parse_nan_sdf(sdf, rt_len, hdr)
assert result is not None, "FAILED: NAN SDF parse returned None"
print(f"  Parsed successfully!")
print(f"    Service ID: {result['service_id'].hex() if result['service_id'] else 'None'}")
print(f"    Service Info: {len(result['service_info'])} bytes")

# ── Test 3: Decode Message Pack from Service Info ────────────────────────

print("\n" + "=" * 60)
print("Test 3: Message Pack decode from NAN Service Info")

si = result['service_info']
assert len(si) >= 3, f"Service Info too short: {len(si)}"
mp_type = si[0] >> 4
msg_size = si[1]
msg_count = si[2]
print(f"  Pack header: type=0x{mp_type:X} msg_size={msg_size} count={msg_count}")
assert mp_type == 0x0F, f"Expected PACK type 0xF, got 0x{mp_type:X}"
assert msg_size == 25, f"Expected msg_size=25, got {msg_size}"
assert msg_count == len(messages), f"Expected {len(messages)} msgs, got {msg_count}"

fields = decode_message_pack(si[3:], msg_count, msg_size)
assert fields is not None, "FAILED: decode_message_pack returned None"
print(f"  Decoded {len(fields)} fields:")
for name, val in fields.items():
    print(f"    {name}: {val}")

# Verify key fields
assert "Basic ID" in fields
assert "NAN_TEST_00001" in fields["Basic ID"]
print(f"\n  ✅ Basic ID matches: {fields['Basic ID']}")

assert "Latitude" in fields
assert abs(fields["Latitude"] - 39.9267) < 0.001, f"Lat mismatch: {fields['Latitude']}"
print(f"  ✅ Latitude: {fields['Latitude']}")

assert "Longitude" in fields
assert abs(fields["Longitude"] - 116.383) < 0.001, f"Lng mismatch: {fields['Longitude']}"
print(f"  ✅ Longitude: {fields['Longitude']}")

# Verify operator/pilot fields
assert "System" in fields, "FAILED: System message missing"
assert "39.9267" in fields["System"], f"System field missing pilot lat: {fields['System']}"
assert "116.383" in fields["System"], f"System field missing pilot lng: {fields['System']}"
assert "25.0m" in fields["System"], f"System field missing operator altitude (25.0m): {fields['System']}"
print(f"  ✅ System (pilot location + altitude): {fields['System']}")

assert "Operator ID" in fields, "FAILED: Operator ID message missing"
assert fields["Operator ID"] == "OP-12345", f"Operator ID mismatch: {fields['Operator ID']}"
print(f"  ✅ Operator ID: {fields['Operator ID']}")

# ── Test 4: NAN Sync Beacon construction ─────────────────────────────────

print("\n" + "=" * 60)
print("Test 4: NAN Sync Beacon construction")

sync_beacon = _build_nan_sync_beacon(drone.mac_address, cluster_id, seq_num=0)
print(f"  Sync Beacon: {len(sync_beacon)} bytes")
# Verify it contains the NAN OUI
assert NAN_OUI in sync_beacon, "FAILED: NAN OUI not found in Sync Beacon"
print(f"  ✅ NAN OUI (50:6F:9A) present in Sync Beacon")

# ── Test 5: GB Message Pack with operator data ───────────────────────────

print("\n" + "=" * 60)
print("Test 5: GB Message Pack (with operator/pilot data)")

gb_pack = build_gb_pack(drone, send_counter=34, proto=1)
print(f"  GB pack: {len(gb_pack)} bytes")
print(f"    counter={gb_pack[0]} type=0x{(gb_pack[1]>>4):X} msg_size={gb_pack[2]} count={gb_pack[3]}")

# Decode the GB pack
fields = decode_message_pack(gb_pack[4:], msg_count=gb_pack[3], msg_size=gb_pack[2])
assert fields is not None, "FAILED: GB pack decode returned None"
print(f"  Decoded {len(fields)} fields:")
for name, val in fields.items():
    print(f"    {name}: {val}")

assert "Basic ID" in fields
assert "NAN_TEST_00001" in fields["Basic ID"]
assert "System" in fields, "FAILED: GB pack missing System message"
assert "Operator ID" in fields, "FAILED: GB pack missing Operator ID"
assert fields["Operator ID"] == "OP-12345", f"GB Operator ID mismatch: {fields['Operator ID']}"
print(f"  ✅ GB pack includes System + Operator ID")

# ── Summary ──────────────────────────────────────────────────────────────

print("\n" + "=" * 60)
print("ALL TESTS PASSED ✅")
print("NAN + GB encoding/decoding pipeline verified end-to-end.")
print("Operator/pilot data confirmed in all message types.")
print("=" * 60)
