#!/usr/bin/env python3
"""Quick GB frame verification."""
from scapy.all import RadioTap, Dot11, Dot11Beacon, Dot11Elt
import struct

OUI = b'\xfa\x0b\xbc'
OUI_TYPE = 0x0D
CAPABILITY = 0x2004

radiotap = RadioTap()
dot11_base = Dot11(type=0, subtype=8, addr1='ff:ff:ff:ff:ff:ff',
                   addr2='02:00:00:00:00:01', addr3='02:00:00:00:00:01', SC=0)
beacon_base = Dot11Beacon(cap=CAPABILITY, beacon_interval=0x0064, timestamp=0)
ie_ssid = Dot11Elt(ID='SSID', info=b'GB-TEST1234')
ie_rates = Dot11Elt(ID='Rates', info=b'\x8c')
vendor_data = OUI + bytes([OUI_TYPE]) + b'\x01\x01\x01'
ie_vendor = Dot11Elt(ID=221, info=vendor_data)

frame = radiotap / dot11_base / beacon_base / ie_ssid / ie_rates / ie_vendor
raw = bytes(frame)

print("Frame hex dump:")
for i in range(0, len(raw), 16):
    print('  ' + ' '.join(f'{b:02x}' for b in raw[i:i+16]))

rt_len = raw[2] | (raw[3] << 8)
mgmt = raw[rt_len:rt_len+24]
fc = struct.unpack('<H', mgmt[0:2])[0]
da = mgmt[4:10]
sa = mgmt[10:16]
bssid = mgmt[16:22]
beacon = raw[rt_len+24:rt_len+36]
bi = struct.unpack('<H', beacon[8:10])[0]
cap = struct.unpack('<H', beacon[10:12])[0]

print()
checks = [
    ('Frame Control 0x0080', fc == 0x0080, f'0x{fc:04x}'),
    ('DA broadcast', da == b'\xff'*6, da.hex(':')),
    ('SA == BSSID', sa == bssid, f'{sa.hex(":")}'),
    ('Beacon Interval 100 TU', bi == 100, str(bi)),
    ('Capability 0x0420', cap == 0x0420, f'0x{cap:04x}'),
]
all_ok = True
for name, ok, detail in checks:
    s = 'PASS' if ok else 'FAIL'
    if not ok: all_ok = False
    print(f'  [{s}] {name} ({detail})')

print(f'\n{"ALL CHECKS PASSED" if all_ok else "SOME FAILED"}')

# Verify IE 221 structure
offset = rt_len + 24 + 12
while offset < len(raw):
    ie_id = raw[offset]
    ie_len = raw[offset + 1]
    if ie_id == 221:
        oui = raw[offset+2:offset+5]
        ot = raw[offset+5]
        print(f'\nVendor IE (221):')
        print(f'  OUI: {oui.hex(":")} {"OK" if oui == b"\\xfa\\x0b\\xbc" else "BAD"}')
        print(f'  OUI Type: 0x{ot:02x} {"OK" if ot == 0x0D else "BAD"}')
    offset += 2 + ie_len
