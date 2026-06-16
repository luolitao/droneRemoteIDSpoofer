"""GB 46750-2025 Wi-Fi beacon transport.

Implements GB 46750-2025 Section 5.2: Operational Identification broadcast
via Wi-Fi Beacon Vendor-Specific IE (Element ID 221).

Uses the same transport mechanism as GB 42590-2023 (OUI=0xFA0BBC, Wi-Fi Beacon)
but with GB 46750-2025 custom binary packet format instead of ASTM Message Pack.

Frame format:
  Element ID    1    221 (0xDD)
  Len           1    Total length from OUI/CID through data
  OUI/CID       3    0xFA0BBC
  Vend Type     1    0x0E  (GB 46750-2025 specific)
  Message Counter 1  0-255, increments per transmission
  GB 46750 Data  var  GB 46750-2025 packet (DataType + Version + Length + Flags + Items)

References:
  - GB 46750-2025 Section 5.2
  - GB 42590-2023 Appendix A1 (Wi-Fi Beacon frame format)
"""

import logging
import struct
import subprocess
import threading
import time
from typing import Dict, List, Tuple

from scapy.all import sendp
import scapy.layers.dot11 as dot11

from drone_rid_spoofer.gb46750_messages import (
    build_gb46750_packet,
    decode_gb46750_packet,
    OperationCategory,
    UAClassification,
    StationLocationType,
    OperationStatus,
    CoordinateSystem,
)
from drone_rid_spoofer.state import DroneState
from drone_rid_spoofer.transport.base import TransportBackend

logger = logging.getLogger(__name__)


class GB46750Backend(TransportBackend):
    """GB 46750-2025 Wi-Fi beacon transport.

    Constructs Wi-Fi Beacon frames with Vendor-Specific IE (ID=221) per
    GB 42590-2023 Appendix A1 frame format, carrying GB 46750-2025
    operational identification data.

    Beacon frame structure:
      RadioTap | Dot11(Beacon) | Dot11Beacon | SSID | Rates | DSset |
      TIM | ERPinfo | ESRates | Vendor(221)

    Vendor IE data layout:
      OUI(FA:0B:BC, 3B) | VendType(0x0E, 1B) | MessageCounter(1B) |
      GB46750Packet(var)
    """

    # ── Constants ──────────────────────────────────────────────────
    OUI = b'\xfa\x0b\xbc'       # OUI/CID 固定取值 0xFA0BBC
    VEND_TYPE = 0x0D            # VDnd Type for GB 46750-2025
    DEST_ADDR = 'ff:ff:ff:ff:ff:ff'
    SSID_PREFIX = 'GB46750-'
    SSID_MAX_LEN = 32
    SUPPORTED_RATES = b'\x82\x84\x8b\x96'
    EXTENDED_SUPPORTED_RATES = b'\x0c\x12\x18\x24\x30\x48\x60\x6c'
    _LOG_BEACON_INTERVAL = 20

    def __init__(self, interface: str, channel: int = 6,
                 beacon_interval: float = 1.0):
        self.interface = interface
        self.channel = channel
        self.beacon_interval = beacon_interval

        self._setup_monitor_mode()
        self._lock_channel()

        # Message Counter: 0-255, wraps at 255
        self._counter = 0

        self._payloads: Dict[bytes, Tuple[dot11.Packet, dot11.Packet, str]] = {}
        self._seq_nums: Dict[bytes, int] = {}
        self._lock = threading.Lock()
        self._running = True
        self._tx_count = 0
        self._thread = threading.Thread(target=self._transmit_loop, daemon=True)
        self._thread.start()

        logger.info(
            f"GB 46750 backend started on {interface} "
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

    def _log_beacon_frame(self, frame, serial: bytes, seq_num: int):
        """Print detailed beacon frame structure."""
        raw = bytes(frame)
        logger.info(f"{'='*60}")
        logger.info(f"  GB 46750 Beacon Frame Dump (Serial={serial.decode('ascii', errors='replace')}, Seq={seq_num})")
        logger.info(f"{'='*60}")

        rt_len = struct.unpack('<H', raw[2:4])[0]

        mgmt_off = rt_len
        mgmt = raw[mgmt_off:mgmt_off + 24]
        fc = struct.unpack('<H', mgmt[0:2])[0]
        da = mgmt[4:10].hex(':')
        sa = mgmt[10:16].hex(':')
        bssid = mgmt[16:22].hex(':')
        sc = struct.unpack('<H', mgmt[22:24])[0]
        logger.info(f"  [802.11] FC=0x{fc:04X} DA={da} SA={sa} BSSID={bssid} SC={sc}")

        beacon_off = mgmt_off + 24

        ie_off = beacon_off + 12
        ie_names = {0: 'SSID', 1: 'Rates', 3: 'DSset', 5: 'TIM', 42: 'ERPinfo',
                    50: 'ESRates', 221: 'Vendor'}
        while ie_off < len(raw):
            ie_id = raw[ie_off]
            ie_len = raw[ie_off + 1]
            ie_data = raw[ie_off + 2:ie_off + 2 + ie_len]
            name = ie_names.get(ie_id, f'Unknown({ie_id})')

            if ie_id == 221:
                oui = ie_data[:3]
                vend_type = ie_data[3]
                counter_val = ie_data[4]
                gb_data = ie_data[5:]

                logger.info(f"  [IE {ie_id} ({name})] len={ie_len}")
                logger.info(f"    OUI/CID={oui.hex(':').upper()}  VendType=0x{vend_type:02X}")
                logger.info(f"    MessageCounter={counter_val}  DataLen={len(gb_data)}")
                logger.info(f"  [Vendor IE] Raw Data: {ie_data[:6].hex()}")
                logger.info(f"  [gb_data] Raw Data: {gb_data[:40].hex()}")

                # Decode GB 46750 packet
                decoded = decode_gb46750_packet(gb_data)
                if decoded:
                    for key, val in decoded.items():
                        if not key.startswith('_'):
                            logger.info(f"    {key}: {val}")
                else:
                    logger.info(f"    (unable to decode GB 46750 packet)")
            elif ie_id == 0:
                ssid_str = ie_data.decode('ascii', errors='replace')
                logger.info(f"  [IE {ie_id} ({name})] len={ie_len}  SSID=\"{ssid_str}\"")
 

            ie_off += 2 + ie_len

        logger.info(f"  Total frame size: {len(raw)} bytes")
        logger.info(f"{'='*60}\n")

    def _transmit_loop(self) -> None:
        """Continuously broadcast all active GB 46750 beacons."""
        logger.info(f"GB 46750 transmit loop started on {self.interface}")
        while self._running:
            packets = []
            current_tsf = int(time.time() * 1_000_000) % (2 ** 64)

            with self._lock:
                items = list(self._payloads.items())

            for serial, (radiotap, ies, mac_addr) in items:
                with self._lock:
                    seq_num = self._seq_nums.get(serial, 0)
                    self._seq_nums[serial] = (seq_num + 1) % 4096

                dot11_base = dot11.Dot11(
                    type=0, subtype=8,
                    addr1=self.DEST_ADDR,
                    addr2=mac_addr,
                    addr3=mac_addr,
                    SC=(seq_num << 4)
                )
                beacon_base = dot11.Dot11Beacon(cap=0, timestamp=current_tsf)

                frame = radiotap / dot11_base / beacon_base / ies
                packets.append(frame)

                if self._tx_count % self._LOG_BEACON_INTERVAL == 0:
                    self._log_beacon_frame(frame, serial, seq_num)
                self._tx_count += 1

            if packets:
                try:
                    sendp(packets, iface=self.interface, verbose=False)
                except Exception as e:
                    logger.debug(f"GB 46750 transmit error: {e}")

            time.sleep(self.beacon_interval)

    def send_messages(self, drone: DroneState, messages: List[bytes]) -> None:
        """Build GB 46750 vendor IE and cache the beacon.

        Vendor IE (ID=221) data layout:
          OUI(3) | VendType(0x0E, 1) | MessageCounter(1) | GB46750Packet(var)
        """
        # Build GB 46750-2025 packet
        gb_packet = build_gb46750_packet(
            serial=drone.serial,
            registration_mark=getattr(drone, 'registration_mark', ''),
            operation_category=getattr(drone, 'operation_category',
                                       OperationCategory.UNDEFINED),
            ua_classification=getattr(drone, 'ua_classification',
                                      UAClassification.MICRO),
            station_location_type=getattr(drone, 'station_location_type',
                                          StationLocationType.TAKEOFF),
            station_lat=drone.pilot_location[0] if drone.pilot_location else None,
            station_lng=drone.pilot_location[1] if drone.pilot_location else None,
            station_altitude=drone.operator_altitude,
            ua_lat=drone.lat,
            ua_lng=drone.lng,
            track_angle=drone.direction,
            ground_speed=drone.speed,
            relative_height=drone.height,
            vertical_speed=drone.vertical_speed,
            geodetic_altitude=drone.geodetic_altitude,
            barometric_altitude=getattr(drone, 'pressure_altitude', None),
            operation_status=OperationStatus.AIR,
            coordinate_system=CoordinateSystem.WGS84,
            horizontal_accuracy=getattr(drone, 'horizontal_accuracy', 0),
            vertical_accuracy=getattr(drone, 'vertical_accuracy', 0),
            speed_accuracy=getattr(drone, 'speed_accuracy', 0),
            timestamp_accuracy=getattr(drone, 'timestamp_accuracy', 0),
        )

        # Vendor IE data (after OUI): VendType | MessageCounter | GB46750Packet
        vendor_data = bytes([self.VEND_TYPE, self._counter]) + gb_packet
        self._counter = (self._counter + 1) & 0xFF

        serial_str = drone.serial.decode('ascii', errors='replace')
        ssid = (self.SSID_PREFIX + serial_str)[:self.SSID_MAX_LEN]

        ie_ssid = dot11.Dot11Elt(ID='SSID', info=ssid)
        ie_rates = dot11.Dot11Elt(ID='Rates', info=self.SUPPORTED_RATES)
        ie_dsset = dot11.Dot11Elt(ID='DSset', info=bytes([self.channel]))
        ie_tim = dot11.Dot11Elt(ID='TIM', info=b'\x00\x01\x00\x00')
        ie_erp = dot11.Dot11Elt(ID='ERPinfo', info=b'\x00')
        ie_esr = dot11.Dot11Elt(ID='ESRates', info=self.EXTENDED_SUPPORTED_RATES)

        ie_vendor = dot11.Dot11Elt(ID=221, info=self.OUI + vendor_data)

        radiotap = dot11.RadioTap()
        ies = ie_ssid / ie_rates / ie_dsset / ie_tim / ie_erp / ie_esr / ie_vendor

        with self._lock:
            self._payloads[drone.serial] = (radiotap, ies, drone.mac_address)

    def close(self) -> None:
        """Stop transmit loop and release resources."""
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        logger.info("GB 46750 backend closed")
