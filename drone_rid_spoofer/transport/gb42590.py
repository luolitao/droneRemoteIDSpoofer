"""GB 42590-2023 Wi-Fi beacon transport.

Implements GB 42590-2023 Appendix A1: Wi-Fi Beacon broadcast with
Vendor-Specific IE (Element ID 221) carrying Remote ID Message Pack.

Frame format (Table A.1):
  Element ID    1    221 (0xDD)
  Len           1    Total length from OUI/CID through Remote ID Messages
  OUI/CID       3    0xFA0BBC
  Vend Type     1    0x0D
  Message Counter 1  0-255, increments per transmission, wraps at 255
  Remote ID Messages  3+N×25  Message Pack (header + N messages of 25 bytes each)

The Message Pack (Remote ID Messages) consists of:
  [MsgType|Proto(1)] [MsgSize=25(1)] [MsgCount(1)] [Messages...]

References:
  - GB 42590-2023 Appendix A1
  - ASTM F3411-22a (ODID Message Pack)
"""

import logging
import struct
import subprocess
import threading
import time
from typing import Dict, List, Tuple

from scapy.all import sendp
import scapy.layers.dot11 as dot11

from drone_rid_spoofer.messages import MsgType, MESSAGE_SIZE
from drone_rid_spoofer.state import DroneState
from drone_rid_spoofer.transport.base import TransportBackend

logger = logging.getLogger(__name__)


class GB42590Backend(TransportBackend):
    """GB 42590-2023 Wi-Fi beacon transport.

    Constructs Wi-Fi Beacon frames with Vendor-Specific IE (ID=221) per
    GB 42590-2023 Appendix A1, carrying ASTM F3411-22a Message Pack data.

    Beacon frame structure:
      RadioTap | Dot11(Beacon) | Dot11Beacon | SSID | Rates | DSset |
      TIM | ERPinfo | ESRates | Vendor(221)

    Vendor IE data layout (per Table A.1):
      OUI(FA:0B:BC, 3B) | VendType(0x0D, 1B) | MessageCounter(1B) |
      MessagePack(3+N×25)
    """

    # ── GB 42590-2023 Table A.1 constants ────────────────────────────
    OUI = b'\xfa\x0b\xbc'       # OUI/CID 固定取值 0xFA0BBC
    VEND_TYPE = 0x0D            # Vend Type 固定取值 0x0D
    DEST_ADDR = 'ff:ff:ff:ff:ff:ff'
    SSID_PREFIX = 'GB42950-'
    SSID_MAX_LEN = 32
    SUPPORTED_RATES = b'\x82\x84\x8b\x96'
    EXTENDED_SUPPORTED_RATES = b'\x0c\x12\x18\x24\x30\x48\x60\x6c'
    _LOG_BEACON_INTERVAL = 5  # 每 N 次发送打印一次 beacon 详情

    def __init__(self, interface: str, channel: int = 6,
                 beacon_interval: float = 1.0):
        self.interface = interface
        self.channel = channel
        self.beacon_interval = beacon_interval

        self._setup_monitor_mode()
        self._lock_channel()

        # Message Counter: 0-255, increments per transmission, wraps at 255
        # (GB 42590 Table A.1: 每发送一条报文消息则加1, 255后从0开始循环)
        self._counter = 0

        self._payloads: Dict[bytes, Tuple[dot11.Packet, dot11.Packet, str]] = {}
        self._seq_nums: Dict[bytes, int] = {}
        self._lock = threading.Lock()
        self._running = True
        self._tx_count = 0
        self._thread = threading.Thread(target=self._transmit_loop, daemon=True)
        self._thread.start()

        logger.info(
            f"GB 42590 backend started on {interface} "
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

    @staticmethod
    def _channel_to_freq(channel: int) -> int:
        """Convert 2.4 GHz Wi-Fi channel to frequency in MHz."""
        return 2412 + (channel - 1) * 5

    def _log_beacon_frame(self, frame, serial: bytes, seq_num: int):
        """Print detailed beacon frame structure matching GB 42590 Table A.1."""
        raw = bytes(frame)
        logger.info(f"{'='*60}")
        logger.info(f"  GB Beacon Frame Dump (Serial={serial.decode('ascii', errors='replace')}, Seq={seq_num})")
        logger.info(f"{'='*60}")

        # Radiotap header
        rt_len = struct.unpack('<H', raw[2:4])[0]
        logger.info(f"  [Radiotap] len={rt_len}  hex={raw[:rt_len].hex()}")

        # 802.11 Header (24 bytes)
        mgmt_off = rt_len
        mgmt = raw[mgmt_off:mgmt_off + 24]
        fc = struct.unpack('<H', mgmt[0:2])[0]
        da = mgmt[4:10].hex(':')
        sa = mgmt[10:16].hex(':')
        bssid = mgmt[16:22].hex(':')
        sc = struct.unpack('<H', mgmt[22:24])[0]
        logger.info(f"  [802.11] FC=0x{fc:04X} DA={da} SA={sa} BSSID={bssid} SC={sc}")

        # Beacon body (12 bytes)
        beacon_off = mgmt_off + 24
        beacon_body = raw[beacon_off:beacon_off + 12]
        ts = struct.unpack('<Q', beacon_body[0:8])[0]
        bi = struct.unpack('<H', beacon_body[8:10])[0]
        cap = struct.unpack('<H', beacon_body[10:12])[0]
        logger.info(f"  [Beacon] TSF={ts} BI={bi}TU Cap=0x{cap:04X}")

        # IEs
        ie_off = beacon_off + 12
        ie_names = {0: 'SSID', 1: 'Rates', 3: 'DSset', 5: 'TIM', 42: 'ERPinfo',
                    50: 'ESRates', 221: 'Vendor'}
        while ie_off < len(raw):
            ie_id = raw[ie_off]
            ie_len = raw[ie_off + 1]
            ie_data = raw[ie_off + 2:ie_off + 2 + ie_len]
            name = ie_names.get(ie_id, f'Unknown({ie_id})')

            if ie_id == 221:  # Vendor IE — GB 42590 Table A.1
                # ie_data layout: OUI(3) | VendType(1) | MessageCounter(1) | MessagePack(3+N×25)
                oui = ie_data[:3]            # OUI/CID: 0xFA0BBC
                vend_type = ie_data[3]       # Vend Type: 0x0D
                counter_val = ie_data[4]     # Message Counter: 0-255
                msg_pack = ie_data[5:]       # Remote ID Messages (Message Pack)
                pack_type = msg_pack[0] >> 4
                pack_proto = msg_pack[0] & 0x0F
                msg_size = msg_pack[1]
                msg_count = msg_pack[2]

                logger.info(f"  [IE {ie_id} ({name})] len={ie_len}")
                logger.info(f"    OUI/CID={oui.hex(':').upper()}  VendType=0x{vend_type:02X}")
                logger.info(f"    MessageCounter={counter_val}  "
                            f"PackType=0x{pack_type:X} Proto={pack_proto}  "
                            f"MsgSize={msg_size} MsgCount={msg_count}")

                # 打印每条消息的摘要
                msg_type_names = {0: 'BasicID', 1: 'Location', 3: 'SelfID', 4: 'System', 5: 'OperatorID'}
                for m in range(msg_count):
                    start = 3 + m * msg_size   # skip 3-byte pack header
                    end = start + msg_size
                    if end > len(msg_pack):
                        break
                    msg = msg_pack[start:end]
                    mt = msg[0] >> 4
                    pv = msg[0] & 0x0F
                    tname = msg_type_names.get(mt, f'Unknown({mt})')
                    if mt == 0x0:  # Basic ID
                        uasid = msg[2:22].rstrip(b'\x00').decode('ascii', errors='replace')
                        logger.info(f"      Msg[{m}] {tname} v{pv}: UASID=\"{uasid}\"")
                    elif mt == 0x1:  # Location
                        lat = struct.unpack('<i', msg[5:9])[0] / 1e7
                        lng = struct.unpack('<i', msg[9:13])[0] / 1e7
                        alt = struct.unpack('<H', msg[15:17])[0] * 0.5 - 1000
                        logger.info(f"      Msg[{m}] {tname} v{pv}: ({lat:.6f}, {lng:.6f}) Alt={alt:.1f}m")
                    elif mt == 0x3:  # Self ID
                        desc = msg[2:25].rstrip(b'\x00').decode('ascii', errors='replace')
                        logger.info(f"      Msg[{m}] {tname} v{pv}: \"{desc}\"")
                    elif mt == 0x4:  # System
                        op_lat = struct.unpack('<i', msg[2:6])[0] / 1e7
                        op_lng = struct.unpack('<i', msg[6:10])[0] / 1e7
                        logger.info(f"      Msg[{m}] {tname} v{pv}: Pilot=({op_lat:.6f}, {op_lng:.6f})")
                    elif mt == 0x5:  # Operator ID
                        op_id = msg[2:22].rstrip(b'\x00').decode('ascii', errors='replace')
                        logger.info(f"      Msg[{m}] {tname} v{pv}: ID=\"{op_id}\"")
                    else:
                        logger.info(f"      Msg[{m}] {tname} v{pv}")
            elif ie_id == 0:  # SSID
                ssid_str = ie_data.decode('ascii', errors='replace')
                logger.info(f"  [IE {ie_id} ({name})] len={ie_len}  SSID=\"{ssid_str}\"")
            else:
                logger.info(f"  [IE {ie_id} ({name})] len={ie_len}  data={ie_data.hex()}")

            ie_off += 2 + ie_len

        logger.info(f"  Total frame size: {len(raw)} bytes")
        logger.info(f"{'='*60}\n")

    def _transmit_loop(self) -> None:
        """Continuously broadcast all active GB beacons."""
        logger.info(f"GB 42590 transmit loop started on {self.interface}")
        while self._running:
            packets = []
            current_tsf = int(time.time() * 1_000_000) % (2 ** 64)

            with self._lock:
                items = list(self._payloads.items())

            for serial, (radiotap, ies, mac_addr) in items:
                with self._lock:
                    seq_num = self._seq_nums.get(serial, 0)
                    self._seq_nums[serial] = (seq_num + 1) % 4096

                # Dynamically instantiate the header to avoid thread contention
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

                # 定期打印 beacon 组包详情
                self._tx_count += 1
                if self._tx_count % self._LOG_BEACON_INTERVAL == 0:
                    self._log_beacon_frame(frame, serial, seq_num)

            if packets:
                try:
                    sendp(packets, iface=self.interface, verbose=False)
                except Exception as e:
                    logger.debug(f"GB 42590 transmit error: {e}")

            time.sleep(self.beacon_interval)

    def send_messages(self, drone: DroneState, messages: List[bytes]) -> None:
        """Build GB 42590 vendor IE per Table A.1 and cache the beacon.

        Vendor IE (ID=221) data layout:
          ┌───────────┬──────┬────────────────────────────────────┐
          │ Field     │ Bytes│ Description                        │
          ├───────────┼──────┼────────────────────────────────────┤
          │ OUI/CID   │  3   │ 0xFA0BBC (固定)                    │
          │ Vend Type │  1   │ 0x0D (固定)                        │
          │ Msg Ctr   │  1   │ 0-255, 每发送一条报文+1, 255后回0   │
          │ Message   │3+N×25│ Message Pack: 头部(3) + N条消息     │
          └───────────┴──────┴────────────────────────────────────┘
        """
        # Vend Type + Message Counter + Message Pack
        msg_count_byte = len(messages) & 0xFF
        # Message Pack header: [MsgType|Proto(1)] [MsgSize(1)] [MsgCount(1)]
        pack_header = bytes([(MsgType.PACK << 4) | 0x01, MESSAGE_SIZE, msg_count_byte])
        
        # Vendor IE data (after OUI): VendType(0x0D) | MessageCounter | MessagePack
        vendor_data = bytes([self.VEND_TYPE, self._counter]) + pack_header + b''.join(messages)
        
        self._counter = (self._counter + 1) & 0xFF  # 循环计数: 255后回到0

        serial_str = drone.serial.decode('ascii', errors='replace')
        ssid = (self.SSID_PREFIX + serial_str)[:self.SSID_MAX_LEN]

        ie_ssid = dot11.Dot11Elt(ID='SSID', info=ssid)
        ie_rates = dot11.Dot11Elt(ID='Rates', info=self.SUPPORTED_RATES)
        ie_dsset = dot11.Dot11Elt(ID='DSset', info=bytes([self.channel]))
        ie_tim = dot11.Dot11Elt(ID='TIM', info=b'\x00\x01\x00\x00')
        ie_erp = dot11.Dot11Elt(ID='ERPinfo', info=b'\x00')
        ie_esr = dot11.Dot11Elt(ID='ESRates', info=self.EXTENDED_SUPPORTED_RATES)

        # IE 221 Vendor Specific: Element ID(221) | Len | OUI(3) | vendor_data
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
        logger.info("GB 42590 backend closed")
