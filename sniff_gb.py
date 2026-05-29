#!/usr/bin/env python3
"""Sniff and analyze GB 42590/46750 Wi-Fi Beacon frames on a monitor-mode interface.

Usage:
    sudo python3 sniff_gb.py <interface> [channel] [--raw]

Example:
    sudo python3 sniff_gb.py wlan0 6          # sniff channel 6, analyze
    sudo python3 sniff_gb.py wlan0 6 --raw    # also dump raw bytes
    sudo python3 sniff_gb.py wlan0            # auto-detect channel

This script:
1. Puts the interface in monitor mode
2. Sets the specified channel
3. Captures 802.11 Beacon frames with Vendor-Specific IE 221
4. Matches OUI FA:0B:BC (ASTM/GB)
5. Decodes and validates every field
"""

import argparse
import os
import signal
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional

try:
    from scapy.all import sniff, Dot11, Dot11Beacon, Dot11Elt, RadioTap
except ImportError:
    print("Scapy not installed. Install with: pip install scapy")
    sys.exit(1)

# ── Constants ──────────────────────────────────────────────────────
OUI_ASTM_GB = b'\xfa\x0b\xbc'
OUI_TYPE_EXPECTED = 0x0D

# TLV tags (GB 46750)
TLV_NAMES = {
    0x01: "Version",
    0x02: "Identifier",
    0x03: "ANSI/CTA-2063-A ID",
    0x04: "Latitude",
    0x05: "Longitude",
    0x06: "Altitude",
    0x07: "Height AGL",
    0x08: "Takeoff Latitude",
    0x09: "Takeoff Longitude",
    0x0A: "Horizontal Speed",
    0x0B: "True Course",
}

# ── Statistics ─────────────────────────────────────────────────────
@dataclass
class Stats:
    total_frames: int = 0
    beacon_frames: int = 0
    oui_match: int = 0
    oui_mismatch: int = 0
    tlv_valid: int = 0
    tlv_errors: List[str] = field(default_factory=list)
    seen_macs: set = field(default_factory=set)
    seen_ouis: set = field(default_factory=set)
    cap_errors: int = 0
    bi_errors: int = 0


stats = Stats()


# ── Message Pack Decoder (ASTM F3411) ──────────────────────────────
def decode_message_pack(data: bytes, msg_count: int, msg_size: int) -> Optional[dict]:
    """Decode ASTM F3411 ODID Message Pack payload (matching opendroneid.h structs).

    'data' is the Message Pack body starting after the 3-byte header:
      [MessageType|ProtoVersion(1)][SingleMsgSize(1)][MsgPackSize(1)]

    Returns a flat dict of all decoded fields across all messages.
    """
    fields = {}
    offset = 0  # data already starts at the first message

    for i in range(msg_count):
        if offset + msg_size > len(data):
            return None
        msg = data[offset:offset + msg_size]
        offset += msg_size

        # Byte 0: MessageType(4b)|ProtoVersion(4b)
        msg_type = msg[0] >> 4
        proto_ver = msg[0] & 0x0F

        if msg_type == 0x00:  # Basic ID
            # Byte 1: [UAType(4b)][IDType(4b)] — C bitfield: UAType LSb first
            ua_type = msg[1] & 0x0F
            id_type = (msg[1] >> 4) & 0x0F
            # Bytes 2-21: UASID (20 bytes, null-padded)
            uas_id = msg[2:22].rstrip(b'\x00').decode('utf-8', errors='replace')
            id_type_names = {0: "None", 1: "Serial", 2: "CAA", 3: "UTM", 4: "Session"}
            ua_type_names = {0: "None", 1: "Aeroplane", 2: "Helicopter/Multirotor", 3: "Gyroplane",
                             4: "HybridLift", 5: "Ornithopter", 6: "Glider", 7: "Kite",
                             8: "FreeBalloon", 9: "CaptiveBalloon", 10: "Airship",
                             11: "FreeFall", 12: "Rocket", 13: "Tethered", 14: "GroundObs", 15: "Other"}
            fields["Basic ID"] = f"{uas_id} (ID={id_type_names.get(id_type, str(id_type))}, UA={ua_type_names.get(ua_type, str(ua_type))})"

        elif msg_type == 0x01:  # Location
            # Byte 1: [SpeedMult(1b)][EWDirection(1b)][HeightType(1b)][Reserved(1b)][Status(4b)]
            # C bitfield: SpeedMult at bit0, Status at bits4-7
            speed_mult = (msg[1] >> 0) & 0x01
            ew_dir = (msg[1] >> 1) & 0x01
            height_type = (msg[1] >> 2) & 0x01
            status = (msg[1] >> 4) & 0x0F
            # Byte 2: Direction (uint8, 0-179 degrees)
            direction_raw = msg[2]
            direction = direction_raw + (180 if ew_dir else 0)
            # Byte 3: SpeedHorizontal (uint8, ×0.25 or ×0.75 m/s)
            if speed_mult:
                speed_h = 255 * 0.25 + msg[3] * 0.75
            else:
                speed_h = msg[3] * 0.25
            # Byte 4: SpeedVertical (int8, ×0.5 m/s)
            speed_v_raw = struct.unpack("<b", msg[4:5])[0]
            speed_v = speed_v_raw * 0.5
            # Bytes 5-8: Latitude (int32 LE, ×10^7)
            lat = struct.unpack("<i", msg[5:9])[0] / 1e7
            # Bytes 9-12: Longitude (int32 LE, ×10^7)
            lng = struct.unpack("<i", msg[9:13])[0] / 1e7
            # Bytes 13-14: AltitudeBaro (uint16 LE, (m+1000)/0.5)
            alt_baro = struct.unpack("<H", msg[13:15])[0] * 0.5 - 1000.0
            # Bytes 15-16: AltitudeGeo (uint16 LE, (m+1000)/0.5)
            alt_geo = struct.unpack("<H", msg[15:17])[0] * 0.5 - 1000.0
            # Bytes 17-18: Height (uint16 LE, (m+1000)/0.5)
            height = struct.unpack("<H", msg[17:19])[0] * 0.5 - 1000.0
            # Byte 19: HorizAccuracy(4b)|VertAccuracy(4b)
            horiz_acc = msg[19] & 0x0F
            vert_acc = (msg[19] >> 4) & 0x0F
            # Bytes 21-22: TimeStamp (uint16 LE, tenths of seconds since the hour)
            timestamp = struct.unpack("<H", msg[21:23])[0] / 10.0

            status_names = {0: "Undeclared", 1: "Ground", 2: "Airborne", 3: "Emergency", 4: "Failure"}
            fields["Status"] = status_names.get(status, f"Unknown({status})")
            fields["Direction"] = direction
            fields["Speed Horizontal"] = f"{speed_h:.2f} m/s"
            fields["Speed Vertical"] = f"{speed_v:.2f} m/s"
            fields["Latitude"] = lat
            fields["Longitude"] = lng
            fields["Altitude Baro"] = f"{alt_baro:.1f} m"
            fields["Altitude Geo"] = f"{alt_geo:.1f} m"
            fields["Height"] = f"{height:.1f} m (above {'ground' if height_type else 'takeoff'})"
            fields["Timestamp"] = f"{timestamp:.1f}s after hour"
            fields["HorizAcc"] = f"enum={horiz_acc}"
            fields["VertAcc"] = f"enum={vert_acc}"

        elif msg_type == 0x02:  # Auth
            fields["Auth"] = f"page={msg[1] & 0x0F}"

        elif msg_type == 0x03:  # Self-ID
            # Byte 1: DescType
            desc_type = msg[1]
            # Bytes 2-24: Description (23 bytes)
            desc = msg[2:25].rstrip(b'\x00').decode('utf-8', errors='replace')
            desc_type_names = {0: "Text", 1: "Emergency", 2: "ExtendedStatus"}
            fields["Self-ID"] = f"{desc} (type={desc_type_names.get(desc_type, desc_type)})"

        elif msg_type == 0x04:  # System
            op_loc_type = msg[1] & 0x03
            class_type = (msg[1] >> 2) & 0x07
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
            # Byte 1: OperatorIdType, Bytes 2-21: OperatorId (20 bytes)
            op_id = msg[2:22].rstrip(b'\x00').decode('utf-8', errors='replace')
            fields["Operator ID"] = op_id

    return fields


# ── TLV Decoder (legacy GB 46750) ──────────────────────────────────
def decode_tlv_payload(data: bytes) -> Optional[dict]:
    """Decode TLV payload and return dict of decoded fields."""
    fields = {}
    pos = 0
    while pos + 2 <= len(data):
        tag = data[pos]
        length = data[pos + 1]
        pos += 2
        if pos + length > len(data):
            return None  # truncated
        value = data[pos:pos + length]
        pos += length

        name = TLV_NAMES.get(tag, f"Unknown(0x{tag:02X})")

        if tag == 0x01:  # Version
            fields[name] = value[0] if value else None
        elif tag == 0x02:  # Identifier
            fields[name] = value.rstrip(b'\x00').decode('utf-8', errors='replace')
        elif tag == 0x03:  # ANSI ID
            fields[name] = value.decode('utf-8', errors='replace')
        elif tag in (0x04, 0x05, 0x08, 0x09):  # Lat/Lng int32 BE ×10^5
            if len(value) == 4:
                fields[name] = struct.unpack(">i", value)[0] / 100000.0
            else:
                fields[name] = f"BAD_LEN({len(value)})"
        elif tag in (0x06, 0x07):  # Alt/Height int16 BE
            if len(value) == 2:
                fields[name] = struct.unpack(">h", value)[0]
            else:
                fields[name] = f"BAD_LEN({len(value)})"
        elif tag == 0x0A:  # Speed int8
            if len(value) == 1:
                fields[name] = struct.unpack(">b", value)[0]
            else:
                fields[name] = f"BAD_LEN({len(value)})"
        elif tag == 0x0B:  # Course uint16 BE
            if len(value) == 2:
                fields[name] = struct.unpack(">H", value)[0]
            else:
                fields[name] = f"BAD_LEN({len(value)})"
        else:
            fields[name] = value.hex()

    return fields


# ── Frame Analyzer ─────────────────────────────────────────────────
def analyze_frame(packet):
    """Analyze a single captured 802.11 frame."""
    stats.total_frames += 1

    # Check if it's a Beacon (type=0, subtype=8)
    if not packet.haslayer(Dot11):
        return
    dot11 = packet[Dot11]
    if dot11.type != 0 or dot11.subtype != 8:
        return

    stats.beacon_frames += 1
    raw = bytes(packet)

    # Extract Radiotap header
    if packet.haslayer(RadioTap):
        rt = packet[RadioTap]
        rt_len = rt.len if hasattr(rt, 'len') else raw[2] | (raw[3] << 8)
    else:
        rt_len = 0

    # 802.11 Management Frame header (24 bytes)
    mgmt = raw[rt_len:rt_len + 24]
    if len(mgmt) < 24:
        return

    fc = struct.unpack('<H', mgmt[0:2])[0]
    da = mgmt[4:10]
    sa = mgmt[10:16]
    bssid = mgmt[16:22]
    seq_ctrl = struct.unpack('<H', mgmt[22:24])[0]
    seq_num = (seq_ctrl >> 4) & 0xFFF
    frag_num = seq_ctrl & 0x0F

    # Beacon body (12 bytes: timestamp(8) + beacon_interval(2) + capability(2))
    beacon_body = raw[rt_len + 24:rt_len + 36]
    if len(beacon_body) < 12:
        return

    timestamp = struct.unpack('<Q', beacon_body[0:8])[0]
    beacon_interval = struct.unpack('<H', beacon_body[8:10])[0]
    capability = struct.unpack('<H', beacon_body[10:12])[0]

    # Check for issues
    issues = []
    if capability != 0x0420:
        stats.cap_errors += 1
        issues.append(f"Capability=0x{capability:04X} (expected 0x0420)")
    if beacon_interval != 100:
        stats.bi_errors += 1
        issues.append(f"Beacon Interval={beacon_interval} TU (expected 100)")

    stats.seen_macs.add(sa.hex(':'))

    # Parse IEs
    ie_offset = rt_len + 36
    ssid = "?"
    supported_rates = []
    vendor_ie_data = None

    pos = ie_offset
    while pos + 2 <= len(raw):
        ie_id = raw[pos]
        ie_len = raw[pos + 1]
        pos += 2
        if pos + ie_len > len(raw):
            break
        ie_value = raw[pos:pos + ie_len]
        pos += ie_len

        if ie_id == 0:  # SSID
            ssid = ie_value.decode('utf-8', errors='replace') if ie_value else "(hidden)"
        elif ie_id == 1:  # Supported Rates
            supported_rates = [b for b in ie_value]
        elif ie_id == 221:  # Vendor-Specific
            vendor_ie_data = ie_value

    # Check for GB/ASTM Vendor IE
    if vendor_ie_data and len(vendor_ie_data) >= 4:
        oui = vendor_ie_data[0:3]
        oui_type = vendor_ie_data[3]
        payload = vendor_ie_data[4:]

        stats.seen_ouis.add(oui.hex(':'))

        if oui == OUI_ASTM_GB:
            stats.oui_match += 1
            mac_str = sa.hex(':')

            print(f"\n{'='*70}")
            print(f"  [GB/ASTM Beacon]  seq={seq_num}  MAC={mac_str}  SSID={ssid}")
            print(f"  Timestamp: {timestamp}  BI: {beacon_interval} TU  Cap: 0x{capability:04X}")
            print(f"  DA: {da.hex(':')}  SA: {sa.hex(':')}  BSSID: {bssid.hex(':')}")
            print(f"  OUI: {oui.hex(':')}  oui_type: 0x{oui_type:02X}  Payload: {len(payload)} bytes")
            if oui_type != OUI_TYPE_EXPECTED:
                print(f"  ⚠ WARNING: oui_type=0x{oui_type:02X}, expected 0x{OUI_TYPE_EXPECTED:02X}")

            # Try Message Pack format first (ASTM F3411), fallback to TLV
            decoded = False
            if len(payload) >= 4:
                # Vendor IE payload: [counter(1)][MessagePack...]
                # MessagePack header: [MsgType|ProtoVer(1)][SingleMsgSize(1)][MsgPackSize(1)]
                msg_counter = payload[0]
                msg_pack_data = payload[1:]  # skip counter
                if len(msg_pack_data) >= 3:
                    mp_header = msg_pack_data[0]
                    mp_msg_type = (mp_header >> 4) & 0x0F
                    single_msg_size = msg_pack_data[1]
                    msg_pack_size = msg_pack_data[2]
                    if mp_msg_type == 0x0F and single_msg_size == 25 and \
                       len(msg_pack_data) >= 3 + msg_pack_size * 25:
                        # Valid Message Pack
                        fields = decode_message_pack(msg_pack_data[3:], msg_pack_size, single_msg_size)
                        if fields:
                            decoded = True
                            stats.tlv_valid += 1
                            print(f"  Message Pack: {msg_pack_size} messages, "
                                  f"counter={msg_counter}")
                            print(f"  Fields:")
                            for name, val in fields.items():
                                if name in ("Latitude", "Longitude", "Takeoff Latitude", "Takeoff Longitude"):
                                    print(f"    {name:25s}: {val:.6f}" if isinstance(val, float) else f"    {name:25s}: {val}")
                                elif name in ("Altitude Baro", "Altitude Geo", "Height", "Speed Horizontal", "Speed Vertical"):
                                    print(f"    {name:25s}: {val} m" if isinstance(val, (int, float)) and name.startswith("Speed") else f"    {name:25s}: {val} m")
                                elif name == "Direction":
                                    print(f"    {name:25s}: {val}°" if isinstance(val, (int, float)) else f"    {name:25s}: {val}")
                                else:
                                    print(f"    {name:25s}: {val}")

            if not decoded:
                # Try TLV format (legacy GB 46750)
                fields = decode_tlv_payload(payload)
                if fields is None:
                    print(f"  ✗ PARSE FAILED — unrecognized format")
                    stats.tlv_errors.append(f"{mac_str}: parse failed")
                else:
                    stats.tlv_valid += 1
                    print(f"  TLV Fields:")
                    for name, val in fields.items():
                        if name in ("Latitude", "Longitude", "Takeoff Latitude", "Takeoff Longitude"):
                            print(f"    {name:25s}: {val:.6f}" if isinstance(val, float) else f"    {name:25s}: {val}")
                        elif name in ("Altitude", "Height AGL"):
                            print(f"    {name:25s}: {val} m" if isinstance(val, int) else f"    {name:25s}: {val}")
                        elif name == "Horizontal Speed":
                            print(f"    {name:25s}: {val} m/s" if isinstance(val, int) else f"    {name:25s}: {val}")
                        elif name == "True Course":
                            print(f"    {name:25s}: {val}°" if isinstance(val, int) else f"    {name:25s}: {val}")
                        else:
                            print(f"    {name:25s}: {val}")

            if issues:
                print(f"  ⚠ Issues: {'; '.join(issues)}")

            # Raw hex dump of IE 221 data
            print(f"  IE 221 raw: {vendor_ie_data.hex(' ')}")

        else:
            stats.oui_mismatch += 1
            # Print first few non-matching for debugging
            if stats.oui_mismatch <= 3:
                print(f"\n  [OTHER Vendor IE] OUI={oui.hex(':')} type=0x{oui_type:02X} "
                      f"MAC={sa.hex(':')} SSID={ssid}")
    elif vendor_ie_data is None:
        # Beacon without IE 221 — skip
        pass


def setup_monitor(iface: str, channel: int):
    """Put interface in monitor mode and set channel."""
    print(f"Setting up {iface} in monitor mode on channel {channel}...")

    # Bring down
    subprocess.run(["sudo", "ip", "link", "set", iface, "down"], check=False, capture_output=True)

    # Set monitor mode
    subprocess.run(["sudo", "iw", "dev", iface, "set", "type", "monitor"], check=False, capture_output=True)

    # Bring up
    subprocess.run(["sudo", "ip", "link", "set", iface, "up"], check=False, capture_output=True)

    # Set channel
    result = subprocess.run(
        ["sudo", "iw", "dev", iface, "set", "channel", str(channel)],
        check=False, capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"  ⚠ Could not set channel {channel}: {result.stderr.strip()}")
    else:
        print(f"  ✓ Channel set to {channel}")

    # Verify mode
    result = subprocess.run(["iw", "dev", iface, "info"], capture_output=True, text=True)
    for line in result.stdout.split('\n'):
        if 'type' in line.lower() or 'channel' in line.lower():
            print(f"  {line.strip()}")


def signal_handler(sig, frame):
    print(f"\n\nStopping...")
    print_summary()
    sys.exit(0)


def print_summary():
    print(f"\n{'='*70}")
    print(f"SUMMARY")
    print(f"  Total frames captured:  {stats.total_frames}")
    print(f"  Beacon frames:          {stats.beacon_frames}")
    print(f"  OUI FA:0B:BC matches:   {stats.oui_match}")
    print(f"  Other OUI (non-GB):     {stats.oui_mismatch}")
    print(f"  TLV decode success:     {stats.tlv_valid}")
    print(f"  Capability errors:      {stats.cap_errors}")
    print(f"  BI errors:              {stats.bi_errors}")
    print(f"  Unique sender MACs:     {len(stats.seen_macs)}")
    print(f"  Seen OUIs:              {[o for o in stats.seen_ouis]}")
    if stats.tlv_errors:
        print(f"  TLV errors:")
        for e in stats.tlv_errors:
            print(f"    - {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Sniff and analyze GB 42590/46750 Wi-Fi Beacon frames"
    )
    parser.add_argument("interface", help="Monitor-mode interface (e.g., wlan0)")
    parser.add_argument("channel", nargs="?", type=int, default=6,
                        help="Wi-Fi channel to listen on (default: 6)")
    parser.add_argument("--timeout", type=int, default=0,
                        help="Stop after N seconds (0 = run until Ctrl+C)")
    parser.add_argument("--no-monitor", action="store_true",
                        help="Skip monitor mode setup (interface already in monitor mode)")

    args = parser.parse_args()

    if os.geteuid() != 0:
        print("ERROR: Must run as root (sudo)")
        sys.exit(1)

    if not args.no_monitor:
        setup_monitor(args.interface, args.channel)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print(f"\nListening on {args.interface} (channel {args.channel})...")
    print("Press Ctrl+C to stop.\n")

    try:
        sniff(
            iface=args.interface,
            prn=analyze_frame,
            store=False,
            timeout=args.timeout or None,
            monitor=True,
        )
    except Exception as e:
        print(f"Sniff error: {e}")

    print_summary()


if __name__ == "__main__":
    main()
