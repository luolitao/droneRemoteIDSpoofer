import logging
import struct
import subprocess
import threading
import time
from typing import List, Dict, Tuple

from scapy.all import sendp
import scapy.layers.dot11 as dot11

from drone_rid_spoofer.state import DroneState
from drone_rid_spoofer.transport.base import TransportBackend

logger = logging.getLogger(__name__)


# ODID Message Pack constants (ASTM F3411 / opendroneid)
ODID_MESSAGE_SIZE = 25  # bytes per encoded message

# Message types (page 0)
ODID_MSGTYPE_BASIC_ID = 0x0
ODID_MSGTYPE_LOCATION = 0x1
ODID_MSGTYPE_AUTH = 0x2
ODID_MSGTYPE_SELF_ID = 0x3
ODID_MSGTYPE_SYSTEM = 0x4
ODID_MSGTYPE_OPERATOR_ID = 0x5

# ID Types for Basic ID message (matching opendroneid.h ODID_idtype)
ODID_IDTYPE_NONE = 0x00
ODID_IDTYPE_SERIAL_NUMBER = 0x01
ODID_IDTYPE_CAA_REGISTRATION = 0x02
ODID_IDTYPE_UTM_ASSIGNED = 0x03
ODID_IDTYPE_SPECIFIC_SESSION = 0x04

# UA Types for Basic ID message
ODID_UATYPE_NONE = 0x00
ODID_UATYPE_AEROPLANE = 0x01
ODID_UATYPE_HELICOPTER = 0x02
ODID_UATYPE_GYROPLANE = 0x03
ODID_UATYPE_HYBRID_LIFT = 0x04
ODID_UATYPE_ORNITHOPTER = 0x05
ODID_UATYPE_GLIDER = 0x06
ODID_UATYPE_KITE = 0x07
ODID_UATYPE_FREE_BALLOON = 0x08
ODID_UATYPE_CAPTIVE_BALLOON = 0x09
ODID_UATYPE_AIRSHIP = 0x0A
ODID_UATYPE_FREE_FALL = 0x0B
ODID_UATYPE_ROCKET = 0x0C
ODID_UATYPE_TETHERED_POWERED = 0x0D
ODID_UATYPE_GROUND_OBSTACLE = 0x0E
ODID_UATYPE_OTHER = 0x0F

ODID_PROTO_VERSION = 0x01
ODID_MESSAGETYPE_PACKED = 0x0F

# Location Status
ODID_STATUS_UNDECLARED = 0x00
ODID_STATUS_EMERGENCY = 0x01
ODID_STATUS_REMOTE_ID_SYSTEM_FAILURE = 0x02


def _encode_basic_id_message(serial: bytes) -> bytes:
    """Encode a Basic ID message (MessageType=0x0, 25 bytes).

    Exact byte layout matching ODID_BasicID_encoded struct (opendroneid.h):
        [0]    MessageType(4b)|ProtoVersion(4b) — 0x0 | 0x2
        [1]    IDType(4b)|UAType(4b)
        [2:22] UASID (20 bytes, null-padded ASCII)
        [22:25] Reserved (3 bytes)

    References:
        opendroneid-core-c libopendroneid/opendroneid.h: ODID_BasicID_encoded
    """
    msg = bytearray(ODID_MESSAGE_SIZE)
    msg[0] = (ODID_MSGTYPE_BASIC_ID << 4) | ODID_PROTO_VERSION
    # Byte 1: [UAType(4b)][IDType(4b)] — C bitfield: UAType first (LSb), IDType second
    msg[1] = (ODID_UATYPE_HELICOPTER & 0x0F) | ((ODID_IDTYPE_SERIAL_NUMBER & 0x0F) << 4)
    # UASID: 20 bytes null-padded
    uasid = serial[:20].ljust(20, b'\x00')
    msg[2:22] = uasid
    # Reserved[3] already zero
    return bytes(msg)


def _encode_location_message(drone: DroneState) -> bytes:
    """Encode a Location message (MessageType=0x1, 25 bytes).

    Exact byte layout matching ODID_Location_encoded struct (opendroneid.h):
        [0]    MessageType(4b)|ProtoVersion(4b) — 0x1 | 0x2
        [1]    SpeedMult(1b)|EWDirection(1b)|HeightType(1b)|Reserved(1b)|Status(4b)
        [2]    Direction (uint8, 0-179 degrees, EWDirection above)
        [3]    SpeedHorizontal (uint8, ×0.25 or ×0.75 m/s depending on SpeedMult)
        [4]    SpeedVertical (int8, ×0.5 m/s)
        [5:9]  Latitude (int32 LE, ×10^7)
        [9:13] Longitude (int32 LE, ×10^7)
        [13:15] AltitudeBaro (uint16 LE, (m + 1000) / 0.5)
        [15:17] AltitudeGeo (uint16 LE, (m + 1000) / 0.5)
        [17:19] Height (uint16 LE, (m + 1000) / 0.5)
        [19]    HorizAccuracy(4b)|VertAccuracy(4b)
        [20]    SpeedAccuracy(4b)|BaroAccuracy(4b)
        [21:23] TimeStamp (uint16 LE, tenths of seconds since the hour)
        [23]    Reserved2(4b)|TSAccuracy(4b)
        [24]    Reserved3

    References:
        opendroneid-core-c libopendroneid/opendroneid.h: ODID_Location_encoded
        opendroneid-core-c libopendroneid/opendroneid.c: encodeLocationMessage()
    """
    msg = bytearray(ODID_MESSAGE_SIZE)

    # Byte 0: MessageType(4b)|ProtoVersion(4b)
    msg[0] = (ODID_MSGTYPE_LOCATION << 4) | ODID_PROTO_VERSION

    # Byte 1: SpeedMult(1b)|EWDirection(1b)|HeightType(1b)|Reserved(1b)|Status(4b)
    # Direction encoding: 0-179 degrees in Direction byte, EWDirection=0 for East (0-179),
    # EWDirection=1 for West (180-359), Direction = degrees - 180 for West.
    direction_deg = drone.direction % 360
    if direction_deg < 180:
        ew_dir = 0
        dir_byte = direction_deg
    else:
        ew_dir = 1
        dir_byte = direction_deg - 180

    # SpeedHorizontal encoding: uint8, ×0.25 m/s (SpeedMult=0)
    speed_h = min(round(drone.speed / 0.25), 255)
    speed_mult = 0

    # SpeedVertical encoding: int8, ×0.5 m/s
    speed_v = max(-127, min(127, round(drone.vertical_speed / 0.5)))

    status = ODID_STATUS_UNDECLARED
    height_type = 0  # above takeoff

    # Byte 1: [SpeedMult(1b)][EWDirection(1b)][HeightType(1b)][Reserved(1b)][Status(4b)]
    # C bitfield: SpeedMult first (bit0, LSb), Status last (bits4-7)
    msg[1] = ((speed_mult & 0x01) << 0) | \
             ((ew_dir & 0x01) << 1) | \
             ((height_type & 0x01) << 2) | \
             (0 << 3) | \
             ((status & 0x0F) << 4)

    # Byte 2: Direction
    msg[2] = dir_byte

    # Byte 3: SpeedHorizontal
    msg[3] = speed_h

    # Byte 4: SpeedVertical
    msg[4] = speed_v & 0xFF

    # Bytes 5-8: Latitude (int32 LE, ×10^7)
    struct.pack_into("<i", msg, 5, int(drone.lat))

    # Bytes 9-12: Longitude (int32 LE, ×10^7)
    struct.pack_into("<i", msg, 9, int(drone.lng))

    # Bytes 13-14: AltitudeBaro (uint16 LE, (m + 1000) / 0.5)
    alt_baro = int(round((drone.pressure_altitude + 1000.0) / 0.5))
    alt_baro = max(0, min(0xFFFF, alt_baro))
    struct.pack_into("<H", msg, 13, alt_baro)

    # Bytes 15-16: AltitudeGeo (uint16 LE, (m + 1000) / 0.5)
    alt_geo = int(round((drone.geodetic_altitude + 1000.0) / 0.5))
    alt_geo = max(0, min(0xFFFF, alt_geo))
    struct.pack_into("<H", msg, 15, alt_geo)

    # Bytes 17-18: Height (uint16 LE, (m + 1000) / 0.5)
    height = int(round((drone.height + 1000.0) / 0.5))
    height = max(0, min(0xFFFF, height))
    struct.pack_into("<H", msg, 17, height)

    # Byte 19: HorizAccuracy(4b)|VertAccuracy(4b) — set to 0 = unknown
    msg[19] = 0x00

    # Byte 20: SpeedAccuracy(4b)|BaroAccuracy(4b) — set to 0 = unknown
    msg[20] = 0x00

    # Bytes 21-22: TimeStamp (uint16 LE, tenths of seconds since the hour)
    from datetime import datetime
    now = datetime.now()
    tenth_seconds = (now.minute * 600 + now.second * 10 + now.microsecond // 100000) % 6000
    struct.pack_into("<H", msg, 21, tenth_seconds)

    # Byte 23: Reserved2(4b)|TSAccuracy(4b) — set to 0
    msg[23] = 0x00

    # Byte 24: Reserved3
    msg[24] = 0x00

    return bytes(msg)


def _encode_self_id_message(serial: bytes) -> bytes:
    """Encode a Self-ID message (MessageType=0x3, 25 bytes).

    Exact byte layout matching ODID_SelfID_encoded struct (opendroneid.h):
        [0]    MessageType(4b)|ProtoVersion(4b) — 0x3 | 0x2
        [1]    DescType (1 byte)
        [2:25] Description (23 bytes, null-padded ASCII)
    """
    msg = bytearray(ODID_MESSAGE_SIZE)
    msg[0] = (ODID_MSGTYPE_SELF_ID << 4) | ODID_PROTO_VERSION
    msg[1] = 0x00  # DescType = text
    desc = b"GB Spoofer"
    msg[2:2 + len(desc)] = desc[:23]
    return bytes(msg)


def _build_gb_payload(drone: DroneState, send_counter: int) -> bytes:
    """Build the ASTM F3411 / GB 42590 Message Pack payload.

    Per GB 42590 Appendix A1, the OUI and oui_type match ASTM F3411
    (OUI=0xFA0BBC, oui_type=0x0D). The payload uses the ASTM F3411
    ODID Message Pack format (opendroneid-core-c).

    Vendor IE payload structure (matches odid_wifi_build_message_pack_beacon_frame):
        [message_counter: 1 byte]
        [MessagePack: variable]
          [MessageType(4b)|ProtoVersion(4b): 1 byte = 0xF|v]
          [SingleMessageSize: 1 byte = 25]
          [MsgPackSize: 1 byte = N]
          [Message 0: 25 bytes] ... [Message N-1: 25 bytes]

    We include 3 messages per pack:
        1. Basic ID (serial number)
        2. Location (lat/lng/alt/speed)
        3. Self-ID (description)

    References:
        - opendroneid-core-c libopendroneid/opendroneid.h:
          ODID_MessagePack_encoded struct
        - opendroneid-core-c libopendroneid/wifi.c:
          odid_wifi_build_message_pack_beacon_frame()
        - opendroneid/wireshark-dissector odid_wifi_sample.pcap
    """
    # Build individual messages
    basic_id_msg = _encode_basic_id_message(drone.serial)
    location_msg = _encode_location_message(drone)
    self_id_msg = _encode_self_id_message(drone.serial)

    messages = basic_id_msg + location_msg + self_id_msg
    msg_count = 3

    # Message Pack header (ODID_MessagePack_encoded):
    #   [MessageType(4b)=0xF|ProtoVersion(4b)][SingleMessageSize=25][MsgPackSize=N]
    msg_pack = bytearray()
    msg_pack.append((ODID_MESSAGETYPE_PACKED << 4) | ODID_PROTO_VERSION)
    msg_pack.append(ODID_MESSAGE_SIZE)
    msg_pack.append(msg_count)
    msg_pack.extend(messages)

    # Full vendor IE payload: [message_counter(1)] + MessagePack
    payload = bytearray()
    payload.append(send_counter & 0xFF)
    payload.extend(msg_pack)

    return bytes(payload)


class GbBackend(TransportBackend):
    """GB 42590-2023 Wi-Fi beacon transport using ASTM F3411 Message Pack format.

    Per GB 42590 Appendix A1, OUI=0xFA0BBC and oui_type=0x0D (matching ASTM F3411).
    The payload uses the ASTM F3411 ODID Message Pack format, making frames
    compatible with all ASTM/GB-compliant receivers (e.g., OpenDroneID apps).

    Frame structure: Wi-Fi Beacon with Vendor-Specific IE 221
    (element ID 221), compatible with any Wi-Fi chipset that supports
    monitor mode + packet injection (e.g., AR9271, MT7610U, RT3070).

    References:
        - GB 42590-2023 Appendix A1
        - ASTM F3411-22a (ODID Message Pack format)
        - opendroneid-core-c libopendroneid/wifi.c:
          odid_wifi_build_message_pack_beacon_frame()
    """

    # GB 42590 Appendix A1: OUI same as ASTM F3411 (FA:0B:BC)
    # GB 46750: same oui_type 0x0D as ASTM, TLV payload
    OUI = b'\xfa\x0b\xbc'
    OUI_TYPE = 0x0D

    DEST_ADDR = 'ff:ff:ff:ff:ff:ff'
    SSID_PREFIX = 'GB-'
    SSID_MAX_LEN = 32

    # Per frdid_wifi_build_beacon_frame: single basic rate 0x8C (6 Mbps)
    SUPPORTED_RATES = b'\x8c'

    # Capability flags matching the C reference:
    # IEEE80211_CAPINFO_SHORT_SLOTTIME (0x0400) | IEEE80211_CAPINFO_SHORT_PREAMBLE (0x0020) = 0x0420
    # NOTE: Scapy's Dot11Beacon.cap uses fmt='!H' (big-endian), but 802.11 wire format
    # is little-endian. So we pass the byte-swapped value so that the wire bytes
    # correctly represent 0x0420 in little-endian.
    CAPABILITY = 0x2004  # byte-swapped: 0x0420 -> wire bytes 20 04 -> le16 = 0x0420

    def __init__(self, interface: str, channel: int = 6,
                 beacon_interval: float = 1.0):
        """Initialize GB 42590 / GB 46750 Wi-Fi beacon backend.

        Args:
            interface: Wi-Fi interface name (e.g., wlan1).
            channel: Wi-Fi channel to broadcast on (default: 6).
            beacon_interval: Seconds between beacon transmissions (default: 1.0s,
                per GB 42590 requirement of 1 transmission per second).
        """
        self.interface = interface
        self.channel = channel
        self.beacon_interval = beacon_interval

        # Set interface to monitor mode for packet injection
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
            logger.info(f"GB interface {self.interface} set to monitor mode")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Could not set monitor mode on {self.interface}: {e}")

        # Lock the physical interface to the target channel
        try:
            subprocess.run(
                ["sudo", "iw", "dev", self.interface, "set", "channel", str(self.channel)],
                check=True, capture_output=True
            )
            logger.info(f"GB interface {self.interface} locked to channel {self.channel}")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Could not lock channel on {self.interface}: {e}")

        # Cached payloads keyed by drone serial
        self._payloads: Dict[bytes, Tuple[dot11.Packet, dot11.Packet, str]] = {}
        self._seq_nums: Dict[bytes, int] = {}
        self._send_counters: Dict[bytes, int] = {}
        self._lock = threading.Lock()
        self._running = True
        self._thread = threading.Thread(target=self._transmit_loop, daemon=True)
        self._thread.start()
        logger.info(
            f"GB 42590 backend started on {interface} "
            f"with {self.beacon_interval*1000:.0f}ms beacon interval"
        )

    def _build_radiotap(self) -> dot11.Packet:
        """Build a Radiotap header with rate + TX flags for reliable injection.

        A minimal Radiotap (8 bytes) works on some drivers but others
        (e.g., Realtek, some MediaTek) require present flags for rate
        and/or TX flags to reliably inject frames. We include:
          - mac_timestamp (8 bytes, zeroed — not critical for injection)
          - Flags (1 byte)
          - Rate (1 byte)
          - Channel (4 bytes: freq + flags)
        """
        return dot11.RadioTap(
            mac_timestamp=0,
            Rate=6,           # 6 Mbps (matches Supported Rates IE)
            ChannelFrequency=2437,  # will be overridden per-channel if needed
            ChannelFlags=0x00a0,    # 2 GHz spectrum, 20 MHz
        )

    def _transmit_loop(self) -> None:
        """Continuously broadcast all active GB beacons."""
        logger.info(f"GB transmit loop started on {self.interface}")
        while self._running:
            packets = []
            current_tsf = int(time.time() * 1_000_000) % (2**64)

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
                beacon_base = dot11.Dot11Beacon(
                    cap=self.CAPABILITY,
                    beacon_interval=0x0064,
                    timestamp=current_tsf
                )

                frame = radiotap / dot11_base / beacon_base / ies
                packets.append(frame)

            if packets:
                # Send frames one at a time with monitor=True for reliable
                # injection across different chipsets (Realtek, MediaTek, Atheros).
                for frame in packets:
                    try:
                        sendp(frame, iface=self.interface, verbose=False,
                              monitor=True)
                    except Exception as e:
                        logger.error(f"GB transmit error: {e}")
            else:
                # No payloads cached yet — send_messages hasn't been called
                pass

            time.sleep(self.beacon_interval)

    def send_messages(self, drone: DroneState, messages: List[bytes]) -> None:
        """Build a GB 42590 beacon with ASTM Message Pack payload.

        Uses the ASTM F3411 ODID Message Pack format inside IE 221
        (OUI=FA:0B:BC, oui_type=0x0D) for compatibility with all
        ASTM/GB-compliant receivers.

        Note: The 'messages' parameter is accepted for interface compatibility
        but is not used directly. The GB payload is built from drone state.
        """
        with self._lock:
            counter = self._send_counters.get(drone.serial, 0)
            self._send_counters[drone.serial] = (counter + 1) % 256

        # Build the ASTM Message Pack payload
        gb_payload = _build_gb_payload(drone, counter)

        serial_str = drone.serial.decode('ascii', errors='replace')
        ssid = (self.SSID_PREFIX + serial_str)[:self.SSID_MAX_LEN]

        # Standard Wi-Fi Beacon IEs — matching odid_wifi_build_message_pack_beacon_frame:
        # Only SSID + single Supported Rate + Vendor-Specific (221)
        ie_ssid = dot11.Dot11Elt(ID='SSID', info=ssid)
        ie_rates = dot11.Dot11Elt(ID='Rates', info=self.SUPPORTED_RATES)

        # GB 42590 / ASTM F3411 Vendor-Specific IE (element ID 221)
        # Format: OUI(3) + oui_type(1) + message_pack_payload...
        vendor_data = self.OUI + bytes([self.OUI_TYPE]) + gb_payload
        ie_vendor = dot11.Dot11Elt(ID=221, info=vendor_data)

        radiotap = self._build_radiotap()
        ies = ie_ssid / ie_rates / ie_vendor

        with self._lock:
            self._payloads[drone.serial] = (radiotap, ies, drone.mac_address)

    def close(self) -> None:
        """Stop the transmit loop and release resources."""
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        logger.info("GB 42590/46750 backend closed")
