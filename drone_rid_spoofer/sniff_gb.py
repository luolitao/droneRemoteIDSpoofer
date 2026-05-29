#!/usr/bin/env python3
"""Sniff and analyze GB 42590 & Wi-Fi NAN Remote ID frames on a monitor-mode interface.

Supports both:
  - GB/ASTM Beacon frames: OUI FA:0B:BC, IE 221, counter + Message Pack
  - Wi-Fi NAN SDF frames:   OUI 50:6F:9A, Public Action Frame, Message Pack in Service Descriptor

Usage:
    sudo python3 sniff_gb.py <interface> [channel] [--nan] [--raw]

Example:
    sudo python3 sniff_gb.py wlan0 6             # sniff GB beacons on channel 6
    sudo python3 sniff_gb.py wlan0 6 --nan       # sniff NAN SDF frames on channel 6
    sudo python3 sniff_gb.py wlan0 6 --all       # sniff both GB and NAN frames
    sudo python3 sniff_gb.py wlan0               # auto-detect channel (default 6)
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

# Import shared ODID decoder
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from drone_rid_spoofer.messages import decode_message_pack

# ── Constants ────────────────────────────────────────────────────────────

# GB/ASTM OUI
OUI_ASTM_GB = b'\xfa\x0b\xbc'
OUI_TYPE_GB = 0x0D

# NAN OUI (Wi-Fi Alliance)
OUI_NAN = b'\x50\x6f\x9a'
OUI_TYPE_NAN = 0x13

# NAN multicast address
NAN_MULTICAST_BYTES = bytes.fromhex('506f9a010000')

# 802.11 frame types
WLAN_TYPE_MGMT = 0x00
WLAN_SUBTYPE_BEACON = 0x80
WLAN_SUBTYPE_ACTION = 0xD0
ACTION_CATEGORY_PUBLIC = 0x04

# NAN attribute IDs
NAN_ATTR_SERVICE_ID_LIST = 0x02
NAN_ATTR_SERVICE_DESCRIPTOR = 0x03

# TLV tags (legacy GB 46750)
TLV_NAMES = {
    0x01: "Version",      0x02: "Identifier",        0x03: "ANSI/CTA-2063-A ID",
    0x04: "Latitude",     0x05: "Longitude",         0x06: "Altitude",
    0x07: "Height AGL",   0x08: "Takeoff Latitude",  0x09: "Takeoff Longitude",
    0x0A: "Horizontal Speed", 0x0B: "True Course",
}


# ── Statistics ───────────────────────────────────────────────────────────

@dataclass
class Stats:
    total_frames: int = 0
    beacon_frames: int = 0
    action_frames: int = 0
    gb_match: int = 0
    nan_match: int = 0
    oui_mismatch: int = 0
    decode_ok: int = 0
    decode_errors: List[str] = field(default_factory=list)
    seen_macs: set = field(default_factory=set)
    seen_ouis: set = field(default_factory=set)

stats = Stats()


# ── Helpers ──────────────────────────────────────────────────────────────

def _fmt_mac(b: bytes) -> str:
    return b.hex(':')

def _parse_radiotap(raw: bytes, packet) -> int:
    """Return radiotap header length from raw bytes or Scapy packet."""
    if packet.haslayer(RadioTap):
        rt = packet[RadioTap]
        return rt.len if hasattr(rt, 'len') else raw[2] | (raw[3] << 8)
    return 0


def _parse_mgmt_header(raw: bytes, rt_len: int) -> Optional[dict]:
    """Parse 802.11 management frame header. Returns dict or None."""
    mgmt = raw[rt_len:rt_len + 24]
    if len(mgmt) < 24:
        return None
    fc = struct.unpack('<H', mgmt[0:2])[0]
    subtype = (fc >> 4) & 0x0F
    ftype = (fc >> 2) & 0x03
    return {
        'fc': fc, 'type': ftype, 'subtype': subtype,
        'da': mgmt[4:10], 'sa': mgmt[10:16], 'bssid': mgmt[16:22],
        'seq_num': (struct.unpack('<H', mgmt[22:24])[0] >> 4) & 0xFFF,
        'frag_num': struct.unpack('<H', mgmt[22:24])[0] & 0x0F,
    }


def _parse_beacon_body(raw: bytes, rt_len: int) -> Optional[dict]:
    """Parse beacon frame body (12 bytes)."""
    body = raw[rt_len + 24:rt_len + 36]
    if len(body) < 12:
        return None
    return {
        'timestamp': struct.unpack('<Q', body[0:8])[0],
        'beacon_interval': struct.unpack('<H', body[8:10])[0],
        'capability': struct.unpack('<H', body[10:12])[0],
    }


def _parse_ies(raw: bytes, ie_start: int) -> List[dict]:
    """Parse information elements from raw bytes."""
    ies = []
    pos = ie_start
    while pos + 2 <= len(raw):
        ie_id = raw[pos]
        ie_len = raw[pos + 1]
        pos += 2
        if pos + ie_len > len(raw):
            break
        ies.append({'id': ie_id, 'len': ie_len, 'value': raw[pos:pos + ie_len]})
        pos += ie_len
    return ies


# ── TLV Decoder (legacy GB 46750) ────────────────────────────────────────

def decode_tlv_payload(data: bytes) -> Optional[dict]:
    """Decode TLV payload (legacy GB 46750 format)."""
    fields = {}
    pos = 0
    while pos + 2 <= len(data):
        tag, length = data[pos], data[pos + 1]
        pos += 2
        if pos + length > len(data):
            return None
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
            fields[name] = struct.unpack(">i", value)[0] / 100000.0 if len(value) == 4 else f"BAD_LEN({len(value)})"
        elif tag in (0x06, 0x07):  # Alt/Height int16 BE
            fields[name] = struct.unpack(">h", value)[0] if len(value) == 2 else f"BAD_LEN({len(value)})"
        elif tag == 0x0A:  # Speed int8
            fields[name] = struct.unpack(">b", value)[0] if len(value) == 1 else f"BAD_LEN({len(value)})"
        elif tag == 0x0B:  # Course uint16 BE
            fields[name] = struct.unpack(">H", value)[0] if len(value) == 2 else f"BAD_LEN({len(value)})"
        else:
            fields[name] = value.hex()
    return fields


# ── NAN SDF Parser ───────────────────────────────────────────────────────

def _parse_nan_sdf(raw: bytes, rt_len: int, hdr: dict) -> Optional[dict]:
    """Parse a NAN Service Discovery Frame and extract the Message Pack.

    NAN SDF is a Public Action Frame:
      Radiotap + 802.11 Action Header + Public Action Body + NAN Attributes

    Returns dict with decoded fields and metadata, or None on parse failure.
    """
    # Action frame header is 24 bytes. Body starts after that.
    body_start = rt_len + 24

    # Action body: Category(1) + ActionCode(1) + OUI(3) + OUI Type(1) = 6 bytes minimum
    if body_start + 6 > len(raw):
        return None
    action_category = raw[body_start]
    action_code = raw[body_start + 1]
    nan_oui = raw[body_start + 2:body_start + 5]
    nan_oui_type = raw[body_start + 6]

    if action_category != ACTION_CATEGORY_PUBLIC:
        return None
    if nan_oui != OUI_NAN:
        return None

    # NAN attributes follow the action body header (offset = body_start + 6)
    attr_pos = body_start + 6
    service_info_bytes = None
    decoded_service_id = None

    while attr_pos + 3 <= len(raw):
        attr_id = raw[attr_pos]
        if attr_id == NAN_ATTR_SERVICE_ID_LIST:
            # Length is 1 byte for this attribute
            attr_len = raw[attr_pos + 1]
            if attr_pos + 2 + attr_len <= len(raw):
                decoded_service_id = raw[attr_pos + 2:attr_pos + 2 + attr_len]
            attr_pos += 2 + attr_len
        elif attr_id == NAN_ATTR_SERVICE_DESCRIPTOR:
            # Length is 2 bytes (LE) for this attribute
            if attr_pos + 3 > len(raw):
                break
            attr_len = struct.unpack('<H', raw[attr_pos + 1:attr_pos + 3])[0]
            sd_body = raw[attr_pos + 3:attr_pos + 3 + attr_len]
            # Parse Service Descriptor body to extract Service Info (Message Pack)
            if len(sd_body) >= 7:
                # Skip: InstanceID(1) + ReqInstanceID(1) + ServiceControl(1)
                #       + MatchFilterLen(1) + MatchFilter(varies) = 0 here
                #       + SvcRespFilterLen(1) + SvcRespFilter(varies) = 0 here
                #       + ServiceInfoLen(1) + ServiceInfo(...)
                sd_pos = 3  # skip first 3 bytes
                # Matching Filter Length
                if sd_pos < len(sd_body):
                    mf_len = sd_body[sd_pos]
                    sd_pos += 1 + mf_len
                # Service Response Filter Length
                if sd_pos < len(sd_body):
                    srf_len = sd_body[sd_pos]
                    sd_pos += 1 + srf_len
                # Service Info Length + Service Info
                if sd_pos < len(sd_body):
                    si_len = sd_body[sd_pos]
                    sd_pos += 1
                    if sd_pos + si_len <= len(sd_body):
                        service_info_bytes = sd_body[sd_pos:sd_pos + si_len]
            attr_pos += 3 + attr_len
        else:
            # Unknown attribute — try length parsing
            if attr_pos + 3 > len(raw):
                break
            attr_len = struct.unpack('<H', raw[attr_pos + 1:attr_pos + 3])[0]
            attr_pos += 3 + attr_len

    if service_info_bytes is None:
        return None

    return {
        'action_category': action_category,
        'action_code': action_code,
        'service_id': decoded_service_id,
        'service_info': service_info_bytes,
    }


def _print_fields(fields: dict) -> None:
    """Pretty-print decoded message fields."""
    for name, val in fields.items():
        if isinstance(val, float):
            if any(k in name for k in ("Latitude", "Longitude")):
                print(f"    {name:25s}: {val:.6f}")
            elif "Speed" in name:
                print(f"    {name:25s}: {val}")
            else:
                print(f"    {name:25s}: {val:.1f}")
        else:
            print(f"    {name:25s}: {val}")


# ── Frame Analyzer ───────────────────────────────────────────────────────

def analyze_frame(packet, args):
    """Analyze a single captured 802.11 frame for GB or NAN Remote ID data."""
    stats.total_frames += 1

    if not packet.haslayer(Dot11):
        return

    raw = bytes(packet)
    rt_len = _parse_radiotap(raw, packet)
    hdr = _parse_mgmt_header(raw, rt_len)
    if hdr is None:
        return

    do_gb = args.gb or args.all or (not args.nan and not args.gb)
    do_nan = args.nan or args.all

    # ── GB/ASTM Beacon (OUI FA:0B:BC) ─────────────────────────────────
    if do_gb and hdr['subtype'] == 0x08:  # Beacon
        _analyze_gb_beacon(raw, rt_len, hdr)

    # ── NAN SDF (Public Action Frame, OUI 50:6F:9A) ──────────────────
    if do_nan and hdr['subtype'] == 0x0D:  # Action frame
        _analyze_nan_sdf(raw, rt_len, hdr)


def _analyze_gb_beacon(raw: bytes, rt_len: int, hdr: dict) -> None:
    """Analyze a GB/ASTM Beacon frame."""
    stats.beacon_frames += 1
    beacon = _parse_beacon_body(raw, rt_len)
    if beacon is None:
        return

    stats.seen_macs.add(_fmt_mac(hdr['sa']))
    ies = _parse_ies(raw, rt_len + 36)
    ssid = "?"
    vendor_ie = None

    for ie in ies:
        if ie['id'] == 0:
            ssid = ie['value'].decode('utf-8', errors='replace') or "(hidden)"
        elif ie['id'] == 221:
            vendor_ie = ie['value']

    if vendor_ie is None or len(vendor_ie) < 4:
        return

    oui = vendor_ie[0:3]
    oui_type = vendor_ie[3]
    payload = vendor_ie[4:]
    stats.seen_ouis.add(oui.hex(':'))

    if oui != OUI_ASTM_GB:
        stats.oui_mismatch += 1
        if stats.oui_mismatch <= 3:
            print(f"\n  [OTHER Vendor IE] OUI={oui.hex(':')} type=0x{oui_type:02X} "
                  f"MAC={_fmt_mac(hdr['sa'])} SSID={ssid}")
        return

    stats.gb_match += 1
    mac_str = _fmt_mac(hdr['sa'])
    issues = []
    if beacon['capability'] != 0x0420:
        issues.append(f"Cap=0x{beacon['capability']:04X}")
    if beacon['beacon_interval'] != 100:
        issues.append(f"BI={beacon['beacon_interval']}")

    print(f"\n{'='*70}")
    print(f"  [GB/ASTM Beacon] seq={hdr['seq_num']} MAC={mac_str} SSID={ssid}")
    print(f"  TS={beacon['timestamp']} BI={beacon['beacon_interval']}TU Cap=0x{beacon['capability']:04X}")
    print(f"  OUI={oui.hex(':')} oui_type=0x{oui_type:02X} payload={len(payload)}B")
    if oui_type != OUI_TYPE_GB:
        print(f"  WARNING: oui_type=0x{oui_type:02X} expected 0x{OUI_TYPE_GB:02X}")

    # Try Message Pack (ASTM F3411), then TLV (legacy GB 46750)
    decoded = False
    if len(payload) >= 4:
        msg_counter = payload[0]
        mp_data = payload[1:]
        if len(mp_data) >= 3:
            mp_type = mp_data[0] >> 4
            msg_size = mp_data[1]
            msg_count = mp_data[2]
            if mp_type == 0x0F and msg_size == 25 and len(mp_data) >= 3 + msg_count * 25:
                fields = decode_message_pack(mp_data[3:], msg_count, msg_size)
                if fields:
                    stats.decode_ok += 1
                    decoded = True
                    print(f"  Message Pack: {msg_count} msgs, counter={msg_counter}")
                    print(f"  Fields:")
                    _print_fields(fields)

    if not decoded:
        fields = decode_tlv_payload(payload)
        if fields is None:
            print(f"  PARSE FAILED")
            stats.decode_errors.append(f"{mac_str}: parse failed")
        else:
            stats.decode_ok += 1
            print(f"  TLV Fields:")
            _print_fields(fields)

    if issues:
        print(f"  Issues: {'; '.join(issues)}")
    print(f"  IE 221 raw: {vendor_ie.hex(' ')}")


def _analyze_nan_sdf(raw: bytes, rt_len: int, hdr: dict) -> None:
    """Analyze a NAN Service Discovery Frame."""
    stats.action_frames += 1

    # Quick pre-filter: NAN SDF goes to the NAN multicast address
    if hdr['da'] != NAN_MULTICAST_BYTES:
        return

    result = _parse_nan_sdf(raw, rt_len, hdr)
    if result is None:
        return

    stats.nan_match += 1
    mac_str = _fmt_mac(hdr['sa'])
    stats.seen_macs.add(mac_str)
    sid_str = result['service_id'].hex() if result['service_id'] else "?"
    si = result['service_info']

    print(f"\n{'='*70}")
    print(f"  [NAN SDF] seq={hdr['seq_num']} MAC={mac_str}")
    print(f"  DA={_fmt_mac(hdr['da'])} SA={_fmt_mac(hdr['sa'])} BSSID={_fmt_mac(hdr['bssid'])}")
    print(f"  Service ID: {sid_str}  Service Info: {len(si)} bytes")

    # Decode the Message Pack inside Service Info
    if len(si) >= 3:
        mp_type = si[0] >> 4
        msg_size = si[1]
        msg_count = si[2]
        if mp_type == 0x0F and msg_size == 25 and len(si) >= 3 + msg_count * 25:
            fields = decode_message_pack(si[3:], msg_count, msg_size)
            if fields:
                stats.decode_ok += 1
                print(f"  Message Pack: {msg_count} messages")
                print(f"  Fields:")
                _print_fields(fields)
                return

    print(f"  Service Info raw: {si.hex()}")
    stats.decode_errors.append(f"{mac_str}: NAN decode failed")


# ── Setup & Main ─────────────────────────────────────────────────────────

def setup_monitor(iface: str, channel: int):
    """Put interface in monitor mode and set channel."""
    print(f"Setting up {iface} in monitor mode on channel {channel}...")
    subprocess.run(["sudo", "ip", "link", "set", iface, "down"], check=False, capture_output=True)
    subprocess.run(["sudo", "iw", "dev", iface, "set", "type", "monitor"], check=False, capture_output=True)
    subprocess.run(["sudo", "ip", "link", "set", iface, "up"], check=False, capture_output=True)
    result = subprocess.run(
        ["sudo", "iw", "dev", iface, "set", "channel", str(channel)],
        check=False, capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  Could not set channel {channel}: {result.stderr.strip()}")
    else:
        print(f"  Channel set to {channel}")

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
    print(f"  Action frames:          {stats.action_frames}")
    print(f"  GB/ASTM matches:        {stats.gb_match}")
    print(f"  NAN SDF matches:        {stats.nan_match}")
    print(f"  Other OUI (non-GB):     {stats.oui_mismatch}")
    print(f"  Decode success:         {stats.decode_ok}")
    print(f"  Unique sender MACs:     {len(stats.seen_macs)}")
    print(f"  Seen OUIs:              {[o for o in stats.seen_ouis]}")
    if stats.decode_errors:
        print(f"  Decode errors:")
        for e in stats.decode_errors:
            print(f"    - {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Sniff GB 42590 & Wi-Fi NAN Remote ID frames"
    )
    parser.add_argument("interface", help="Monitor-mode interface (e.g., wlan0)")
    parser.add_argument("channel", nargs="?", type=int, default=6,
                        help="Wi-Fi channel (default: 6)")
    parser.add_argument("--nan", action="store_true",
                        help="Sniff NAN Service Discovery Frames (OUI 50:6F:9A)")
    parser.add_argument("--gb", action="store_true",
                        help="Sniff GB/ASTM Beacon frames only (OUI FA:0B:BC)")
    parser.add_argument("--all", action="store_true",
                        help="Sniff both GB and NAN frames (default)")
    parser.add_argument("--timeout", type=int, default=0,
                        help="Stop after N seconds (0 = until Ctrl+C)")
    parser.add_argument("--no-monitor", action="store_true",
                        help="Skip monitor mode setup")
    parser.add_argument("--raw", action="store_true",
                        help="Dump raw bytes of IE 221 data")

    args = parser.parse_args()

    # Default: sniff both GB and NAN if no specific flag given
    if not args.nan and not args.gb:
        args.all = True

    if os.geteuid() != 0:
        print("ERROR: Must run as root (sudo)")
        sys.exit(1)

    if not args.no_monitor:
        setup_monitor(args.interface, args.channel)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    modes = []
    if args.all or args.gb:
        modes.append("GB/ASTM Beacons")
    if args.all or args.nan:
        modes.append("NAN SDF")
    print(f"\nListening on {args.interface} (ch {args.channel}) for {' + '.join(modes)}...")
    print("Press Ctrl+C to stop.\n")

    try:
        sniff(
            iface=args.interface,
            prn=lambda pkt: analyze_frame(pkt, args),
            store=False,
            timeout=args.timeout or None,
            monitor=True,
        )
    except Exception as e:
        print(f"Sniff error: {e}")

    print_summary()


if __name__ == "__main__":
    main()
