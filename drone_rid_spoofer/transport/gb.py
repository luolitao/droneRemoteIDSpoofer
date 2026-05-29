"""GB 42590-2023 Wi-Fi beacon transport using ASTM F3411 ODID Message Pack.

OUI=0xFA0BBC, oui_type=0x0D (matching ASTM F3411 per GB 42590 Appendix A1).
Compatible with all ASTM/GB-compliant receivers (e.g., OpenDroneID apps).

References:
  - GB 42590-2023 Appendix A1
  - ASTM F3411-22a (ODID Message Pack)
  - opendroneid-core-c libopendroneid/wifi.c
"""

import logging
import subprocess
import threading
import time
from typing import Dict, Tuple

from scapy.all import sendp
import scapy.layers.dot11 as dot11

from drone_rid_spoofer.state import DroneState
from drone_rid_spoofer.transport.base import TransportBackend
from drone_rid_spoofer.odid_encoding import build_message_pack

logger = logging.getLogger(__name__)


class GbBackend(TransportBackend):
    """GB 42590 Wi-Fi beacon transport using ASTM F3411 Message Pack format.

    Frame: Wi-Fi Beacon with Vendor-Specific IE 221 carrying ODID Message Pack.
    Works with any Wi-Fi chipset supporting monitor mode + packet injection.
    """

    OUI = b'\xfa\x0b\xbc'
    OUI_TYPE = 0x0D
    DEST_ADDR = 'ff:ff:ff:ff:ff:ff'
    SSID_PREFIX = 'GB-'
    SSID_MAX_LEN = 32
    SUPPORTED_RATES = b'\x8c'  # 6 Mbps
    CAPABILITY = 0x2004  # byte-swapped 0x0420 (short slot + short preamble)

    def __init__(self, interface: str, channel: int = 6,
                 beacon_interval: float = 1.0):
        self.interface = interface
        self.channel = channel
        self.beacon_interval = beacon_interval

        self._setup_monitor_mode()
        self._lock_channel()

        self._payloads: Dict[bytes, Tuple[dot11.Packet, dot11.Packet, str]] = {}
        self._seq_nums: Dict[bytes, int] = {}
        self._send_counters: Dict[bytes, int] = {}
        self._lock = threading.Lock()
        self._running = True
        self._thread = threading.Thread(target=self._transmit_loop, daemon=True)
        self._thread.start()

        logger.info(
            f"GB backend started on {interface} "
            f"with {self.beacon_interval*1000:.0f}ms beacon interval"
        )

    def _setup_monitor_mode(self):
        """Set interface to monitor mode for packet injection."""
        try:
            subprocess.run(
                ["sudo", "ip", "link", "set", self.interface, "down"],
                check=False, capture_output=True
            )
            subprocess.run(
                ["sudo", "iw", "dev", self.interface, "set", "type", "monitor"],
                check=True, capture_output=True
            )
            subprocess.run(
                ["sudo", "ip", "link", "set", self.interface, "up"],
                check=False, capture_output=True
            )
            logger.info(f"Interface {self.interface} set to monitor mode")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Could not set monitor mode on {self.interface}: {e}")

    def _lock_channel(self):
        """Lock interface to target channel."""
        try:
            subprocess.run(
                ["sudo", "iw", "dev", self.interface, "set", "channel", str(self.channel)],
                check=True, capture_output=True
            )
            logger.info(f"Interface {self.interface} locked to channel {self.channel}")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Could not lock channel on {self.interface}: {e}")

    def _build_radiotap(self) -> dot11.Packet:
        """Build Radiotap header with rate + TX flags for reliable injection."""
        return dot11.RadioTap(
            mac_timestamp=0,
            Rate=6,
            ChannelFrequency=2437,
            ChannelFlags=0x00a0,
        )

    def _transmit_loop(self) -> None:
        """Continuously broadcast all active GB beacons."""
        logger.info(f"GB transmit loop started on {self.interface}")
        while self._running:
            packets = []
            current_tsf = int(time.time() * 1_000_000) % (2 ** 64)

            with self._lock:
                items = list(self._payloads.items())

            for serial, (radiotap, ies, mac_addr) in items:
                with self._lock:
                    seq_num = self._seq_nums.get(serial, 0)
                    self._seq_nums[serial] = (seq_num + 1) % 4096

                frame = (
                    radiotap
                    / dot11.Dot11(type=0, subtype=8,
                                   addr1=self.DEST_ADDR, addr2=mac_addr, addr3=mac_addr,
                                   SC=(seq_num << 4))
                    / dot11.Dot11Beacon(cap=self.CAPABILITY, beacon_interval=0x0064,
                                        timestamp=current_tsf)
                    / ies
                )
                packets.append(frame)

            for frame in packets:
                try:
                    sendp(frame, iface=self.interface, verbose=False, monitor=True)
                except Exception as e:
                    logger.error(f"GB transmit error: {e}")

            time.sleep(self.beacon_interval)

    def send_messages(self, drone: DroneState, messages: list) -> None:
        """Build and cache a GB beacon with ASTM Message Pack payload."""
        with self._lock:
            counter = self._send_counters.get(drone.serial, 0)
            self._send_counters[drone.serial] = (counter + 1) % 256

        gb_payload = build_message_pack(drone, counter)
        serial_str = drone.serial.decode('ascii', errors='replace')
        ssid = (self.SSID_PREFIX + serial_str)[:self.SSID_MAX_LEN]

        vendor_data = self.OUI + bytes([self.OUI_TYPE]) + gb_payload
        ies = (
            dot11.Dot11Elt(ID='SSID', info=ssid)
            / dot11.Dot11Elt(ID='Rates', info=self.SUPPORTED_RATES)
            / dot11.Dot11Elt(ID=221, info=vendor_data)
        )

        with self._lock:
            self._payloads[drone.serial] = (self._build_radiotap(), ies, drone.mac_address)

    def close(self) -> None:
        """Stop transmit loop and release resources."""
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        logger.info("GB backend closed")
