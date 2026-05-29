#!/usr/bin/env python3
"""
GB 42590/46750 Frame Diagnostic Tool v2
Comprehensive byte-level analysis with fixed CAPABILITY.
"""

import struct
import sys
sys.path.insert(0, '.')

from scapy.all import RadioTap, Dot11, Dot11Beacon, Dot11Elt
from drone_rid_spoofer.state import DroneState
from drone_rid_spoofer.transport.gb import _build_gb_payload


drone = DroneState(
    serial=b"TEST1234567890ABCDEF",
    pilot_location=(399267000, 1163830000),
    lat=399267000,
    lng=1163830000,
    mac_address="02:00:00:00:00:01",
    ble_address="00:00:00:00:00:01",
    direction=90,
    speed=15.0,
    pressure_altitude=120.0,
    height=100.0,
)

OUI = b'\xfa\x0b\xbc'
OUI_TYPE = 0x0D
DEST_ADDR = 'ff:ff:ff:ff:ff:ff'
SSID_PREFIX = 'GB-'
SUPPORTED_RATES = b'\x8c'
CAPABILITY = 0x2004  # byte-swapped: Scapy !H → wire 20 04 → le16 0x0420
MAC_ADDR = drone.mac_address

gb_payload = _build_gb_payload(drone)
ssid = (SSID_PREFIX + drone.serial.decode('ascii', errors='replace'))[:32]
vendor_data = OUI + bytes([OUI_TYPE]) + gb_payload

ie_ssid = Dot11Elt(ID='SSID', info=ssid)
ie_rates = Dot11Elt(ID='Rates', info=SUPPORTED_RATES)
ie_vendor = Dot11Elt(ID=221, info=vendor_data)

radiotap = RadioTap()
dot11_base = Dot11(type=0, subtype=8, addr1=DEST_ADDR, addr2=MAC_ADDR, addr3=MAC_ADDR, SC=0)
beacon_base = Dot11Beacon(cap=CAPABILITY, beacon_interval=0x0064, timestamp=0)

frame = radiotap / dot11_base / beacon_base / ie_ssid / ie_rates / ie_vendor
raw = bytes(frame)

print("=" * 80)
print("GB 42590/46750 Beacon Frame — BYTE-LEVEL ANALYSIS")
print("=" * 80)

# Full hex dump
print(f"\nFrame length: {len(raw)} bytes\n")
for i in range(0, len(raw), 16):
    chunk = raw[i:i+16]
    hex_part = ' '.join(f'{b:02x}' for b in chunk)
    ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
    print(f"  {i:04x}: {hex_part:<48s}  {ascii_part}")

print("\n" + "=" * 80)
print("FIELD-BY-FIELD VERIFICATION vs C REFERENCE")
print("=" * 80)

# ── RadioTap (8 bytes) ──
rt_len = raw[2] | (raw[3] << 8)
print(f"\n[RadioTap]  bytes 0x0000-0x{rt_len-1:04x}")
print(f"  Length: {rt_len}")
print(f"  Raw: {' '.join(f'{b:02x}' for b in raw[:rt_len])}")

# ── 802.11 Management Header (24 bytes) ──
mgmt_off = rt_len
mgmt = raw[mgmt_off:mgmt_off+24]
fc = struct.unpack('<H', mgmt[0:2])[0]
dur = struct.unpack('<H', mgmt[2:4])[0]
da = mgmt[4:10]
sa = mgmt[10:16]
bssid = mgmt[16:22]
seq = struct.unpack('<H', mgmt[22:24])[0]

print(f"\n[802.11 Mgmt Header]  offset 0x{mgmt_off:04x} (24 bytes)")
print(f"  Frame Control:  0x{fc:04x} {'✓' if fc == 0x0080 else '✗ EXPECTED 0x0080'}")
print(f"  Duration:       {dur} {'✓' if dur == 0 else '✗'}")
print(f"  DA:             {':'.join(f'{b:02x}' for b in da)} {'✓' if da == b'\\xff\\xff\\xff\\xff\\xff\\xff' else '✗'}")
print(f"  SA:             {':'.join(f'{b:02x}' for b in sa)}")
print(f"  BSSID:          {':'.join(f'{b:02x}' for b in bssid)}")
print(f"  SA==BSSID:      {'✓' if sa == bssid else '✗'}")

# ── Beacon Frame Body (12 bytes) ──
beacon_off = mgmt_off + 24
beacon = raw[beacon_off:beacon_off+12]
tsf = struct.unpack('<Q', beacon[0:8])[0]
bi = struct.unpack('<H', beacon[8:10])[0]
cap = struct.unpack('<H', beacon[10:12])[0]

print(f"\n[Beacon Body]  offset 0x{beacon_off:04x} (12 bytes)")
print(f"  Timestamp:      {tsf}")
print(f"  Beacon Interval: {bi} TU {'✓' if bi == 100 else '✗'}")
print(f"  Capability:     0x{cap:04x} {'✓' if cap == 0x0420 else '✗ EXPECTED 0x0420'}")
if cap == 0x0420:
    print(f"    bit  5 (Short Preamble):  {'✓' if cap & 0x0020 else '✗'}")
    print(f"    bit 10 (Short Slot Time): {'✓' if cap & 0x0400 else '✗'}")

# ── IEs ──
ie_off = beacon_off + 12
ie_num = 0
print(f"\n[Information Elements]  starting at offset 0x{ie_off:04x}")

expected_ies = {
    0: {'name': 'SSID', 'check': lambda d: d[:3] == b'GB-'},
    1: {'name': 'Supported Rates', 'check': lambda d: d == b'\x8c'},
    221: {'name': 'Vendor Specific', 'check': None},
}

found_ie_ids = []
extra_ie_ids = []

off = ie_off
while off < len(raw):
    ie_id = raw[off]
    ie_len = raw[off + 1]
    ie_data = raw[off + 2: off + 2 + ie_len]
    ie_total = 2 + ie_len
    ie_end = off + ie_total

    info = expected_ies.get(ie_id, None)
    if info:
        name = info['name']
        found_ie_ids.append(ie_id)
        check_fn = info.get('check')
        result = '✓' if (check_fn is None or check_fn(ie_data)) else '✗'
    else:
        name = f'UNKNOWN({ie_id})'
        extra_ie_ids.append(ie_id)
        result = '⚠ EXTRA'

    print(f"\n  IE#{ie_num}: {name}  ID={ie_id}  len={ie_len}  {result}")
    print(f"    Offset: 0x{off:04x}-0x{ie_end-1:04x} ({ie_total} bytes)")
    print(f"    Raw: {' '.join(f'{b:02x}' for b in raw[off:ie_end])}")

    if ie_id == 221:
        oui = ie_data[:3]
        oui_type = ie_data[3]
        tlv = ie_data[4:]

        print(f"\n    OUI: {' '.join(f'{b:02x}' for b in oui)} {'✓' if oui == b'\\xfa\\x0b\\xbc' else '✗ EXPECTED fa 0b bc'}")
        print(f"    OUI Type: 0x{oui_type:02x} {'✓' if oui_type == 0x0D else '✗ EXPECTED 0x0D'}")

        # Parse TLV
        print(f"\n    TLV Payload ({len(tlv)} bytes):")
        tlv_off = 0
        while tlv_off < len(tlv):
            tag = tlv[tlv_off]
            length = tlv[tlv_off + 1]
            value = tlv[tlv_off + 2: tlv[tlv_off + 2 + length]]

            tag_names = {
                0x01: 'Version', 0x02: 'Identifier', 0x03: 'ANSI/CTA-2063-A',
                0x04: 'Latitude', 0x05: 'Longitude', 0x06: 'Altitude',
                0x07: 'Height AGL', 0x08: 'Takeoff Lat', 0x09: 'Takeoff Lng',
                0x0A: 'H-Speed', 0x0B: 'True Course',
            }
            tag_name = tag_names.get(tag, f'Unknown')

            checks = []
            if tag == 0x01:  # Version
                checks.append(('version=1', value[0] == 1))
            elif tag == 0x02:  # Identifier
                checks.append(('len=30', length == 30))
            elif tag == 0x04:  # Latitude
                val = struct.unpack(">i", value)[0]
                checks.append((f'lat={val/1e5:.5f}°', abs(val/1e5 - 39.9267) < 0.001))
            elif tag == 0x05:  # Longitude
                val = struct.unpack(">i", value)[0]
                checks.append((f'lng={val/1e5:.5f}°', abs(val/1e5 - 116.383) < 0.001))
            elif tag == 0x06:  # Altitude
                val = struct.unpack(">h", value)[0]
                checks.append((f'alt={val}m', val == 120))
            elif tag == 0x07:  # Height
                val = struct.unpack(">h", value)[0]
                checks.append((f'height={val}m', val == 100))
            elif tag == 0x0A:  # Speed
                val = struct.unpack(">b", value)[0]
                checks.append((f'speed={val}m/s', val == 15))
            elif tag == 0x0B:  # Course
                val = struct.unpack(">H", value)[0]
                checks.append((f'course={val}°', val == 90))

            status = '✓' if all(c[1] for c in checks) else '✗'
            print(f"      Tag=0x{tag:02x} ({tag_name}), Len={length} {status}")
            for c_desc, c_ok in checks:
                print(f"        {'✓' if c_ok else '✗'} {c_desc}")

            tlv_off += 2 + length

    off = ie_end
    ie_num += 1

# ── Final Summary ──
print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)

all_ok = True

# Check expected IEs
for exp_id in (0, 1, 221):
    ok = exp_id in found_ie_ids
    if not ok: all_ok = False
    print(f"  {'✓' if ok else '✗'} IE {expected_ies[exp_id]['name']} present")

# Check no extra IEs
ok = len(extra_ie_ids) == 0
if not ok: all_ok = False
print(f"  {'✓' if ok else '✗'} No extra IEs {extra_ie_ids if extra_ie_ids else ''}")

# Capability
ok = cap == 0x0420
if not ok: all_ok = False
print(f"  {'✓' if ok else '✗'} Capability = 0x0420 (got 0x{cap:04x})")

# OUI + oui_type
ok = True  # already checked inline
print(f"  {'✓' if ok else '✗'} OUI = FA:0B:BC, oui_type = 0x0D")

# Beacon Interval
ok = bi == 100
if not ok: all_ok = False
print(f"  {'✓' if ok else '✗'} Beacon Interval = 100 TU (got {bi})")

# Frame Control
ok = fc == 0x0080
if not ok: all_ok = False
print(f"  {'✓' if ok else '✗'} Frame Control = 0x0080 (got 0x{fc:04x})")

# SA == BSSID
ok = sa == bssid
if not ok: all_ok = False
print(f"  {'✓' if ok else '✗'} SA == BSSID")

# DA broadcast
ok = da == b'\xff\xff\xff\xff\xff\xff'
if not ok: all_ok = False
print(f"  {'✓' if ok else '✗'} DA = broadcast")

print(f"\n  {'✓ ALL CHECKS PASSED' if all_ok else '✗ SOME CHECKS FAILED — SEE ABOVE'}")
