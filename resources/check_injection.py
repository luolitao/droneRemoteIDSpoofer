#!/usr/bin/env python3
"""Check Wi-Fi interface injection capability.

Usage:
    sudo python3 check_injection.py <interface>
"""

import argparse
import ctypes
import os
import socket
import struct
import subprocess
import sys

# Linux wireless extension constants
SIOCSIWMODE = 0x8B06
SIOCGIWMODE = 0x8B07
SIOCSIWFREQ = 0x8B04
SIOCGIWNAME = 0x8B01

IW_MODE_AUTO = 0
IW_MODE_ADHOC = 1
IW_MODE_INFRA = 2
IW_MODE_MASTER = 3
IW_MODE_REPEAT = 4
IW_MODE_SECOND = 5
IW_MODE_MONITOR = 6

MODE_NAMES = {
    0: "Auto", 1: "Ad-Hoc", 2: "Managed",
    3: "Master/AP", 4: "Repeater", 5: "Secondary", 6: "Monitor"
}


def check_interface(iface: str):
    """Comprehensive check of a Wi-Fi interface."""
    print(f"{'='*70}")
    print(f"Interface: {iface}")
    print(f"{'='*70}")

    # 1. iw info
    print(f"\n1. iw dev info:")
    result = subprocess.run(["iw", "dev", iface, "info"],
                            capture_output=True, text=True)
    for line in result.stdout.split('\n'):
        line = line.strip()
        if line:
            print(f"   {line}")

    # 2. ethtool driver info
    print(f"\n2. Driver:")
    result = subprocess.run(["ethtool", "-i", iface],
                            capture_output=True, text=True)
    for line in result.stdout.split('\n'):
        line = line.strip()
        if line:
            print(f"   {line}")

    # 3. Check if it supports monitor mode
    print(f"\n3. Monitor mode support:")

    # Try to get current mode via iw
    current_type = None
    for line in result.stdout.split('\n'):
        if 'type' in line.lower():
            current_type = line.strip()

    # Try setting monitor mode
    subprocess.run(["sudo", "ip", "link", "set", iface, "down"],
                   check=False, capture_output=True)
    result = subprocess.run(
        ["sudo", "iw", "dev", iface, "set", "type", "monitor"],
        check=False, capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"   ✓ Monitor mode supported")
    else:
        print(f"   ✗ Monitor mode NOT supported: {result.stderr.strip()}")

    subprocess.run(["sudo", "ip", "link", "set", iface, "up"],
                   check=False, capture_output=True)

    # 4. Check chipset (from lsusb for USB adapters)
    print(f"\n4. USB device check:")
    result = subprocess.run(["lsusb"], capture_output=True, text=True)
    for line in result.stdout.split('\n'):
        line = line.strip()
        if line and 'network' in line.lower() or 'wireless' in line.lower() or 'wlan' in line.lower():
            print(f"   {line}")

    # Also check for known chipset identifiers
    known_chipsets = {
        'ath9k_htc': 'Atheros AR9271/AR7010 (✓ injection supported)',
        'rt2800usb': 'Ralink RT3070/RT5370 (✓ injection supported)',
        'rtl88x2bu': 'Realtek RTL8812BU (⚠ injection varies)',
        'rtl88x2cu': 'Realtek RTL8812CU (⚠ injection varies)',
        'rtl8188eu': 'Realtek RTL8188EU (⚠ injection varies)',
        'mt76x0u': 'MediaTek MT7610U (✓ injection supported)',
        'mt76x2u': 'MediaTek MT7612U (✓ injection supported)',
        'brcmfmac': 'Broadcom (✗ no injection)',
    }

    # Check loaded modules
    result = subprocess.run(["lsmod"], capture_output=True, text=True)
    for module, desc in known_chipsets.items():
        if module in result.stdout:
            print(f"\n5. Detected driver: {module} → {desc}")

    # 6. Try actual injection test with scapy
    print(f"\n6. Scapy injection test:")
    try:
        from scapy.all import sendp, RadioTap, Dot11, Dot11Beacon, Dot11Elt, conf

        # Ensure interface is in monitor mode
        subprocess.run(["sudo", "ip", "link", "set", iface, "down"],
                       check=False, capture_output=True)
        subprocess.run(["sudo", "iw", "dev", iface, "set", "type", "monitor"],
                       check=False, capture_output=True)
        subprocess.run(["sudo", "ip", "link", "set", iface, "up"],
                       check=False, capture_output=True)

        # Build minimal test frame
        test_frame = (
            RadioTap() /
            Dot11(type=0, subtype=8, addr1='ff:ff:ff:ff:ff:ff',
                  addr2='02:00:00:00:00:99', addr3='02:00:00:00:00:99') /
            Dot11Beacon(cap=0x2004, beacon_interval=0x0064) /
            Dot11Elt(ID='SSID', info=b'INJECTION-TEST') /
            Dot11Elt(ID='Rates', info=b'\x8c')
        )

        try:
            sendp(test_frame, iface=iface, count=3, verbose=True)
            print(f"   ✓ sendp() completed without error")
        except Exception as e:
            print(f"   ✗ sendp() failed: {e}")
            # Try with monitor=True
            try:
                sendp(test_frame, iface=iface, count=3, verbose=True, monitor=True)
                print(f"   ✓ sendp(monitor=True) completed")
            except Exception as e2:
                print(f"   ✗ sendp(monitor=True) also failed: {e2}")

    except ImportError:
        print(f"   Scapy not available, skipping")

    # 7. Summary
    print(f"\n{'='*70}")
    print(f"SUMMARY")
    print(f"{'='*70}")
    print(f"  Run this on your RECEIVE interface to verify injection:")
    print(f"    sudo python3 send_and_capture.py {iface} <rx_iface> 6")


def main():
    parser = argparse.ArgumentParser(description="Check Wi-Fi interface injection capability")
    parser.add_argument("interface", help="Interface name (e.g., wlan1)")
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("ERROR: Must run as root (sudo)")
        sys.exit(1)

    check_interface(args.interface)


if __name__ == "__main__":
    main()
