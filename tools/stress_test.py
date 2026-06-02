#!/usr/bin/env python3
"""Continuously send GB beacons (same logic as GB42590Backend) and verify on RX.

Usage:
    sudo python3 stress_test.py wlan1 wlan2 6
"""

import argparse
import os
import struct
import subprocess
import sys
import threading
import time

try:
    from scapy.all import sniff, sendp, Dot11, Dot11Beacon, Dot11Elt, RadioTap
except ImportError:
    print("Scapy not installed.")
    sys.exit(1)

OUI = b'\xfa\x0b\xbc'
OUI_TYPE = 0x0D
CAPABILITY = 0x2004
DEST_ADDR = 'ff:ff:ff:ff:ff:ff'
SRC_MAC = '02:00:00:00:00:AA'


def build_gb_payload():
    buf = bytearray()

    def _append_tlv(tag, value):
        buf.append(tag)
        buf.append(len(value))
        buf.extend(value)

    _append_tlv(0x01, b'\x01')
    _append_tlv(0x02, b'STRESS-TEST-001'.ljust(30, b'\x00'))
    _append_tlv(0x03, b'STRESS-TEST-001')
    _append_tlv(0x04, struct.pack(">i", 3123000))
    _append_tlv(0x05, struct.pack(">i", 12147000))
    _append_tlv(0x06, struct.pack(">h", 100))
    _append_tlv(0x07, struct.pack(">h", 80))
    _append_tlv(0x08, struct.pack(">i", 3123000))
    _append_tlv(0x09, struct.pack(">i", 12147000))
    _append_tlv(0x0A, struct.pack(">b", 5))
    _append_tlv(0x0B, struct.pack(">H", 90))
    return bytes(buf)


def build_beacon(seq):
    rt = RadioTap(mac_timestamp=0, Rate=6, ChannelFrequency=2437, ChannelFlags=0x00a0)
    d11 = Dot11(type=0, subtype=8, addr1=DEST_ADDR, addr2=SRC_MAC,
                addr3=SRC_MAC, SC=(seq << 4))
    bcn = Dot11Beacon(cap=CAPABILITY, beacon_interval=0x0064,
                      timestamp=int(time.time() * 1_000_000) % (2**64))
    payload = build_gb_payload()
    vendor = Dot11Elt(ID=221, info=OUI + bytes([OUI_TYPE]) + payload)
    return rt / d11 / bcn / Dot11Elt(ID='SSID', info=b'GB-STRESS') / \
           Dot11Elt(ID='Rates', info=b'\x8c') / vendor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tx_iface")
    parser.add_argument("rx_iface")
    parser.add_argument("channel", nargs="?", type=int, default=6)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--interval", type=float, default=0.05)
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("ERROR: Must run as root")
        sys.exit(1)

    # Setup RX
    subprocess.run(["sudo", "ip", "link", "set", args.rx_iface, "down"],
                   check=False, capture_output=True)
    subprocess.run(["sudo", "iw", "dev", args.rx_iface, "set", "type", "monitor"],
                   check=False, capture_output=True)
    subprocess.run(["sudo", "ip", "link", "set", args.rx_iface, "up"],
                   check=False, capture_output=True)
    subprocess.run(["sudo", "iw", "dev", args.rx_iface, "set", "channel",
                    str(args.channel)], check=False, capture_output=True)
    subprocess.run(["sudo", "iw", "dev", args.tx_iface, "set", "channel",
                    str(args.channel)], check=False, capture_output=True)

    captured = []
    capture_done = threading.Event()

    def sniffer(pkt):
        if OUI in bytes(pkt):
            captured.append(bytes(pkt))

    def sniff_thread():
        sniff(iface=args.rx_iface, prn=sniffer, store=False,
              timeout=args.count * args.interval + 3, monitor=True)
        capture_done.set()

    cap_t = threading.Thread(target=sniff_thread, daemon=True)
    cap_t.start()
    time.sleep(0.5)  # Let sniff start

    # ── Send continuously ──
    print(f"Sending {args.count} beacons on {args.tx_iface} every {args.interval*1000:.0f}ms...")
    sent = 0
    for i in range(args.count):
        frame = build_beacon(i % 4096)
        try:
            sendp(frame, iface=args.tx_iface, verbose=False, monitor=True)
            sent += 1
        except Exception as e:
            print(f"  send error at seq {i}: {e}")
        time.sleep(args.interval)

    print(f"Sent: {sent}/{args.count}")

    # Wait for capture to finish
    capture_done.wait(timeout=5)
    print(f"Captured: {len(captured)}/{args.count} ({len(captured)*100//max(1,args.count)}%)")

    if captured:
        raw = captured[0]
        # Quick validation
        rt_len = raw[2] | (raw[3] << 8)
        cap = struct.unpack('<H', raw[rt_len + 34:rt_len + 36])[0]
        bi = struct.unpack('<H', raw[rt_len + 32:rt_len + 34])[0]
        print(f"First frame: {len(raw)} bytes, cap=0x{cap:04X}, BI={bi}")
        print(f"Seq numbers seen: {len(captured)}")
    else:
        print("❌ NO FRAMES CAPTURED")


if __name__ == "__main__":
    main()
