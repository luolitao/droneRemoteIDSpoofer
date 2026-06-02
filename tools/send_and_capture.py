#!/usr/bin/env python3
"""Send a single GB 42590/46750 beacon and simultaneously capture it locally.

Usage:
    sudo python3 send_and_capture.py <tx_iface> <rx_iface> [channel]

This is the most direct test: send one frame and immediately check if it
appears on the air.
"""

import argparse
import ctypes
import os
import struct
import subprocess
import sys
import threading
import time

try:
    from scapy.all import (
        sniff, sendp,
        Dot11, Dot11Beacon, Dot11Elt, RadioTap,
        conf,
    )
except ImportError:
    print("Scapy not installed. Install with: pip install scapy")
    sys.exit(1)


# ── Build the exact same frame as GB42590Backend ────────────────────────

OUI = b'\xfa\x0b\xbc'
OUI_TYPE = 0x0D
CAPABILITY = 0x2004  # byte-swapped for Scapy's !H cap field
DEST_ADDR = 'ff:ff:ff:ff:ff:ff'
SRC_MAC = '02:00:00:00:00:01'
SSID = b'GB-TEST1234'
SUPPORTED_RATES = b'\x8c'


def build_gb_payload():
    """Build a test TLV payload matching _build_gb_payload in gb42590.py."""
    buf = bytearray()

    def _append_tlv(tag, value):
        buf.append(tag)
        buf.append(len(value))
        buf.extend(value)

    # 0x01: Version = 1
    _append_tlv(0x01, b'\x01')

    # 0x02: Identifier (30 bytes)
    identifier = b'TEST-DRONE-001'.ljust(30, b'\x00')
    _append_tlv(0x02, identifier)

    # 0x03: ANSI/CTA-2063-A ID
    _append_tlv(0x03, b'TEST-DRONE-001')

    # 0x04: Latitude (31.23° × 10^5 = 3123000)
    _append_tlv(0x04, struct.pack(">i", 3123000))

    # 0x05: Longitude (121.47° × 10^5 = 12147000)
    _append_tlv(0x05, struct.pack(">i", 12147000))

    # 0x06: Altitude = 100m
    _append_tlv(0x06, struct.pack(">h", 100))

    # 0x07: Height AGL = 80m
    _append_tlv(0x07, struct.pack(">h", 80))

    # 0x08: Takeoff Lat
    _append_tlv(0x08, struct.pack(">i", 3123000))

    # 0x09: Takeoff Lng
    _append_tlv(0x09, struct.pack(">i", 12147000))

    # 0x0A: Horizontal Speed = 5 m/s
    _append_tlv(0x0A, struct.pack(">b", 5))

    # 0x0B: True Course = 90°
    _append_tlv(0x0B, struct.pack(">H", 90))

    return bytes(buf)


def build_beacon(seq_num: int = 0):
    """Build a single GB beacon frame exactly as GB42590Backend does."""
    gb_payload = build_gb_payload()

    radiotap = RadioTap()
    dot11_base = Dot11(
        type=0, subtype=8,
        addr1=DEST_ADDR,
        addr2=SRC_MAC,
        addr3=SRC_MAC,
        SC=(seq_num << 4),
    )
    beacon_base = Dot11Beacon(
        cap=CAPABILITY,
        beacon_interval=0x0064,
        timestamp=int(time.time() * 1_000_000) % (2**64),
    )
    ie_ssid = Dot11Elt(ID='SSID', info=SSID)
    ie_rates = Dot11Elt(ID='Rates', info=SUPPORTED_RATES)
    vendor_data = OUI + bytes([OUI_TYPE]) + gb_payload
    ie_vendor = Dot11Elt(ID=221, info=vendor_data)

    frame = radiotap / dot11_base / beacon_base / ie_ssid / ie_rates / ie_vendor
    return frame


# ── Frame Validation ────────────────────────────────────────────────

def validate_frame(raw: bytes, label: str = "Frame") -> dict:
    """Validate frame byte-by-byte. Returns dict of findings."""
    findings = {"errors": [], "warnings": [], "info": []}

    if len(raw) < 60:
        findings["errors"].append(f"Frame too short: {len(raw)} bytes")
        return findings

    # Radiotap header
    rt_version = raw[0]
    rt_pad = raw[1]
    rt_len = raw[2] | (raw[3] << 8)
    findings["info"].append(f"Radiotap: version={rt_version} pad={rt_pad} len={rt_len}")

    if rt_len < 8 or rt_len > len(raw):
        findings["errors"].append(f"Bad Radiotap length: {rt_len}")
        return findings

    # 802.11 MAC header (24 bytes)
    mgmt_start = rt_len
    mgmt = raw[mgmt_start:mgmt_start + 24]
    if len(mgmt) < 24:
        findings["errors"].append("802.11 header truncated")
        return findings

    fc = struct.unpack('<H', mgmt[0:2])[0]
    duration = struct.unpack('<H', mgmt[2:4])[0]
    da = mgmt[4:10]
    sa = mgmt[10:16]
    bssid = mgmt[16:22]
    seq_ctrl = struct.unpack('<H', mgmt[22:24])[0]

    type_val = (fc >> 2) & 0x3
    subtype_val = (fc >> 4) & 0xF

    findings["info"].append(
        f"802.11: FC=0x{fc:04X} type={type_val} subtype={subtype_val} "
        f"DA={da.hex(':')} SA={sa.hex(':')} BSSID={bssid.hex(':')} seq={(seq_ctrl>>4)&0xFFF}"
    )

    if type_val != 0:
        findings["errors"].append(f"Expected Management frame (type=0), got type={type_val}")
    if subtype_val != 8:
        findings["errors"].append(f"Expected Beacon (subtype=8), got subtype={subtype_val}")
    if da != b'\xff' * 6:
        findings["errors"].append(f"DA not broadcast: {da.hex(':')}")

    # Beacon body (12 bytes)
    beacon_start = mgmt_start + 24
    beacon_body = raw[beacon_start:beacon_start + 12]
    if len(beacon_body) < 12:
        findings["errors"].append("Beacon body truncated")
        return findings

    timestamp = struct.unpack('<Q', beacon_body[0:8])[0]
    bi = struct.unpack('<H', beacon_body[8:10])[0]
    cap = struct.unpack('<H', beacon_body[10:12])[0]

    findings["info"].append(
        f"Beacon: ts={timestamp} BI={bi}TU cap=0x{cap:04X}"
    )

    if bi != 100:
        findings["warnings"].append(f"Beacon Interval = {bi} TU (expected 100)")
    if cap != 0x0420:
        findings["errors"].append(f"Capability = 0x{cap:04X} (expected 0x0420)")

    # Parse IEs
    ie_start = beacon_start + 12
    pos = ie_start
    ie_count = 0
    vendor_found = False

    while pos + 2 <= len(raw):
        ie_id = raw[pos]
        ie_len = raw[pos + 1]
        pos += 2
        if pos + ie_len > len(raw):
            findings["errors"].append(f"IE {ie_id} truncated at offset {pos}")
            break
        ie_value = raw[pos:pos + ie_len]
        pos += ie_len
        ie_count += 1

        if ie_id == 0:  # SSID
            findings["info"].append(f"  IE SSID: len={ie_len} value={ie_value}")
        elif ie_id == 1:  # Rates
            findings["info"].append(f"  IE Rates: len={ie_len} value={ie_value.hex(' ')}")
        elif ie_id == 221:  # Vendor-Specific
            vendor_found = True
            oui = ie_value[0:3]
            oui_type = ie_value[3]
            tlv_data = ie_value[4:]
            findings["info"].append(
                f"  IE 221: OUI={oui.hex(':')} type=0x{oui_type:02X} "
                f"TLV_len={len(tlv_data)}"
            )
            if oui != OUI:
                findings["errors"].append(
                    f"Vendor IE OUI={oui.hex(':')} (expected {OUI.hex(':')})"
                )
            if oui_type != OUI_TYPE:
                findings["errors"].append(
                    f"oui_type=0x{oui_type:02X} (expected 0x{OUI_TYPE:02X})"
                )

    findings["info"].append(f"Total IEs: {ie_count}")
    if not vendor_found:
        findings["errors"].append("No Vendor-Specific IE (221) found!")

    return findings


# ── Main ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Send a single GB beacon and capture/validate it"
    )
    parser.add_argument("tx_iface", help="Transmit interface")
    parser.add_argument("rx_iface", help="Receive interface (monitor mode)")
    parser.add_argument("channel", nargs="?", type=int, default=6)
    parser.add_argument("--count", type=int, default=5,
                        help="Number of beacons to send (default: 5)")
    parser.add_argument("--interval", type=float, default=0.1,
                        help="Interval between beacons in seconds (default: 0.1)")

    args = parser.parse_args()

    if os.geteuid() != 0:
        print("ERROR: Must run as root (sudo)")
        sys.exit(1)

    # Setup RX interface in monitor mode
    print(f"Setting up {args.rx_iface} in monitor mode...")
    subprocess.run(["sudo", "ip", "link", "set", args.rx_iface, "down"],
                   check=False, capture_output=True)
    subprocess.run(["sudo", "iw", "dev", args.rx_iface, "set", "type", "monitor"],
                   check=False, capture_output=True)
    subprocess.run(["sudo", "ip", "link", "set", args.rx_iface, "up"],
                   check=False, capture_output=True)

    # Set same channel on both
    for iface in [args.tx_iface, args.rx_iface]:
        subprocess.run(
            ["sudo", "iw", "dev", iface, "set", "channel", str(args.channel)],
            check=False, capture_output=True
        )
        print(f"  {iface} -> channel {args.channel}")

    # Verify TX interface
    print(f"\nInterface states:")
    for iface in [args.tx_iface, args.rx_iface]:
        result = subprocess.run(["iw", "dev", iface, "info"],
                                capture_output=True, text=True)
        for line in result.stdout.split('\n'):
            line = line.strip()
            if any(kw in line.lower() for kw in ['type', 'channel', 'interface']):
                print(f"  {iface}: {line}")

    # Build the frame
    frame = build_beacon(seq_num=0)
    raw = bytes(frame)

    print(f"\n{'='*70}")
    print(f"BUILT FRAME ANALYSIS (before sending)")
    print(f"{'='*70}")
    print(f"Frame size: {len(raw)} bytes")
    print(f"Hex dump (first 96 bytes):")
    for i in range(0, min(len(raw), 96), 16):
        line = ' '.join(f'{b:02x}' for b in raw[i:i+16])
        print(f"  {i:04x}: {line}")
    if len(raw) > 96:
        print(f"  ... ({len(raw) - 96} more bytes)")

    findings = validate_frame(raw, "built")
    print(f"\nValidation:")
    for kind in ["errors", "warnings", "info"]:
        for msg in findings[kind]:
            prefix = {"errors": "✗", "warnings": "⚠", "info": "  "}[kind]
            print(f"  {prefix} {msg}")

    if findings["errors"]:
        print(f"\n❌ FRAME HAS ERRORS — fix before testing on air!")
        return 1

    # ── Start capture thread ──
    captured = []
    capture_started = threading.Event()

    def capture_callback(pkt):
        raw_pkt = bytes(pkt)
        # Quick filter: check for FA:0B:BC OUI
        if OUI in raw_pkt:
            captured.append(raw_pkt)

    def do_capture():
        capture_started.set()
        sniff(iface=args.rx_iface, prn=capture_callback,
              store=False, timeout=args.count * args.interval + 2,
              monitor=True)

    cap_thread = threading.Thread(target=do_capture, daemon=True)
    cap_thread.start()
    capture_started.wait()

    # ── Send beacons ──
    print(f"\n{'='*70}")
    print(f"SENDING {args.count} beacons on {args.tx_iface} (ch {args.channel})")
    print(f"{'='*70}")

    for i in range(args.count):
        f = build_beacon(seq_num=i)
        sendp(f, iface=args.tx_iface, verbose=False)
        print(f"  Sent beacon seq={i}")
        time.sleep(args.interval)

    print(f"\nWaiting for capture to finish...")
    cap_thread.join(timeout=3)

    # ── Results ──
    print(f"\n{'='*70}")
    print(f"CAPTURE RESULTS")
    print(f"{'='*70}")
    print(f"Captured frames matching OUI FA:0B:BC: {len(captured)}")

    if len(captured) == 0:
        print(f"\n❌ NO GB BEACONS CAPTURED!")
        print(f"\nPossible causes:")
        print(f"  1. TX interface not actually injecting packets")
        print(f"     → Check: sudo iw dev {args.tx_iface} info")
        print(f"     → TX interface must support monitor mode + packet injection")
        print(f"     → Try: sudo aireplay-ng --test {args.tx_iface}")
        print(f"  2. TX and RX on different channels")
        print(f"     → Both set to channel {args.channel}")
        print(f"  3. TX power too low or antenna issue")
        print(f"  4. Radiotap header format incompatible with driver")
        return 1

    for i, raw_pkt in enumerate(captured):
        print(f"\n  Captured frame #{i+1} ({len(raw_pkt)} bytes):")
        # Show hex
        for j in range(0, min(len(raw_pkt), 128), 16):
            line = ' '.join(f'{b:02x}' for b in raw_pkt[j:j+16])
            print(f"    {j:04x}: {line}")

        # Validate captured frame
        cap_findings = validate_frame(raw_pkt, f"captured #{i+1}")
        print(f"\n  Captured frame validation:")
        for kind in ["errors", "warnings", "info"]:
            for msg in cap_findings[kind]:
                prefix = {"errors": "✗", "warnings": "⚠", "info": "  "}[kind]
                print(f"    {prefix} {msg}")

    if len(captured) == args.count:
        print(f"\n✅ ALL {args.count} BEACONS CAPTURED SUCCESSFULLY!")
    else:
        print(f"\n⚠ Only captured {len(captured)}/{args.count} beacons")

    return 0


if __name__ == "__main__":
    sys.exit(main())
