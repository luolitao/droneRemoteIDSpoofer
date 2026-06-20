# wifi.py (重构后)

import logging
import subprocess
import threading
import time
from typing import List, Dict, Tuple

from scapy.all import sendp
import scapy.layers.dot11 as dot11

from drone_rid_spoofer.state import DroneState
from drone_rid_spoofer.messages import MsgType, build_gb_pack
from drone_rid_spoofer.transport.base import TransportBackend

logger = logging.getLogger(__name__)


class WifiBackend(TransportBackend):
    """Wi-Fi beacon frame transport for ASTM F3411, GB42590, and GB46750."""

    APP_CODE = 0x0D
    DEST_ADDR = 'ff:ff:ff:ff:ff:ff'
    SSID_PREFIX = 'RID-'
    SSID_MAX_LEN = 32
    OUI = b'\xfa\x0b\xbc'           # ASTM / common OUI (can be reused)
    SUPPORTED_RATES = b'\x82\x84\x8b\x96'
    EXTENDED_SUPPORTED_RATES = b'\x0c\x12\x18\x24\x30\x48\x60\x6c'

    def __init__(self, interface: str, ess: bool = False, protocol_version: int = 2,
                 channel: int = 6, beacon_interval: float = 0.1024):
        self.interface = interface
        self.ess = ess
        self.protocol_version = protocol_version
        self.channel = channel
        self.beacon_interval = beacon_interval

        # Lock channel
        try:
            subprocess.run(["sudo", "iw", "dev", self.interface, "set", "channel", str(self.channel)], check=True)
            logger.info(f"Wi-Fi interface {self.interface} locked to channel {self.channel}")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Could not lock channel on {self.interface}: {e}")

        # ASTM Pack header (for protocol astm)
        self.pack_header_prefix = bytes([(MsgType.PACK << 4) | self.protocol_version, 0x19])

        # Counters
        self._astm_counter = 0                     # for APP_CODE in ASTM
        self._gb42590_counters: Dict[bytes, int] = {}   # per-drone counter for GB42590
        self._gb46750_counters: Dict[bytes, int] = {}   # 新增
        self._lock = threading.Lock()

        # Payload cache for beacon thread
        self._payloads: Dict[bytes, Tuple[dot11.Packet, dot11.Packet, str]] = {}
        self._seq_nums: Dict[bytes, int] = {}
        self._running = True
        self._thread = threading.Thread(target=self._transmit_loop, daemon=True)
        self._thread.start()
        logger.info(f"Wi-Fi backend started on {interface} with {self.beacon_interval*1000:.0f}ms beacon interval")

    def _transmit_loop(self) -> None:
        """Continuously broadcast all active beacons at the specified interval."""
        while self._running:
            packets = []
            current_tsf = int(time.time() * 1000000) % (2**64)

            with self._lock:
                items = list(self._payloads.items())

            for serial, (radiotap, ies, mac_addr) in items:
                with self._lock:
                    seq_num = self._seq_nums.get(serial, 0)
                    self._seq_nums[serial] = (seq_num + 1) % 4096

                # Dynamically instantiate the header to avoid thread contention on shared Scapy objects
                dot11_base = dot11.Dot11(
                    type=0, subtype=8,
                    addr1=self.DEST_ADDR,
                    addr2=mac_addr,
                    addr3=mac_addr,
                    SC=(seq_num << 4)
                )
                beacon_base = dot11.Dot11Beacon(cap='ESS' if self.ess else 0, timestamp=current_tsf)

                frame = radiotap / dot11_base / beacon_base / ies
                packets.append(frame)

            if packets:
                try:
                    sendp(packets, iface=self.interface, verbose=False)
                except Exception as e:
                    logger.debug(f"Wi-Fi transmit error: {e}")

            time.sleep(self.beacon_interval)

    def send_messages(self, drone: DroneState, messages: List[bytes], protocol: str = "astm") -> None:
        """Pack messages into a Wi-Fi beacon vendor-specific IE according to protocol."""
        if protocol == "astm":
            vendor_data = self._build_astm_vendor_data(messages)
        elif protocol == "gb42590":
            vendor_data = self._build_gb42590_vendor_data(drone)
        elif protocol == "gb46750":
            vendor_data = self._build_gb46750_vendor_data(drone, messages)
        else:
            raise ValueError(f"Unsupported protocol '{protocol}' for Wi-Fi backend")

        serial_str = drone.serial.decode('ascii', errors='replace')
        ssid = (self.SSID_PREFIX + serial_str)[: self.SSID_MAX_LEN]
        ie_ssid = dot11.Dot11Elt(ID='SSID', info=ssid)
        ie_rates = dot11.Dot11Elt(ID='Rates', info=self.SUPPORTED_RATES)
        ie_dsset = dot11.Dot11Elt(ID='DSset', info=bytes([self.channel]))
        ie_tim = dot11.Dot11Elt(ID='TIM', info=b'\x00\x01\x00\x00')
        ie_erp = dot11.Dot11Elt(ID='ERPinfo', info=b'\x00')
        ie_esr = dot11.Dot11Elt(ID='ESRates', info=self.EXTENDED_SUPPORTED_RATES)

        # Vendor IE with our OUI + vendor_data
        ie_vendor = dot11.Dot11Elt(ID=221, info=self.OUI + vendor_data)

        radiotap = dot11.RadioTap()
        ies = ie_ssid / ie_rates / ie_dsset / ie_tim / ie_erp / ie_esr / ie_vendor

        # if protocol == "gb46750":
        # ie_bytes = bytes(ie_vendor)
        # logger.info(f"Vendor IE: {ie_bytes.hex()}")
        
        with self._lock:
            self._payloads[drone.serial] = (radiotap, ies, drone.mac_address)

    def _build_astm_vendor_data(self, messages: List[bytes]) -> bytes:
        """Build ASTM vendor data: APP_CODE + counter + Pack header + msg_count + messages."""
        msg_count = bytes([len(messages) & 0xFF])
        header = bytes([self.APP_CODE, self._astm_counter]) + self.pack_header_prefix + msg_count
        self._astm_counter = (self._astm_counter + 1) & 0xFF
        return header + b''.join(messages)

    def _build_gb42590_vendor_data(self, drone: DroneState) -> bytes:
        """Build GB42590 vendor data using build_gb_pack from messages.py."""
        with self._lock:
            counter = self._gb42590_counters.get(drone.serial, 0)
            self._gb42590_counters[drone.serial] = (counter + 1) & 0xFF
        # build_gb_pack returns the full vendor data (including Vend Type and counter)
        return build_gb_pack(drone, counter, proto=1)   # proto 1 for GB42590

    def _build_gb46750_vendor_data(self, drone: DroneState, messages: List[bytes]) -> bytes:
        """Build GB46750 vendor data: APP_CODE (0x0D) + counter + payload."""
        with self._lock:
            counter = self._gb46750_counters.get(drone.serial, 0)
            self._gb46750_counters[drone.serial] = (counter + 1) & 0xFF
        payload = messages[0] if messages else b''
        return bytes([self.APP_CODE, counter]) + payload

    def close(self) -> None:
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        logger.info("Wi-Fi backend closed")