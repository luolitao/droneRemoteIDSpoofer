"""Wi-Fi NAN (Neighbor Awareness Networking) transport backend for ASTM F3411-22.

Implements the Wi-Fi NAN broadcast method defined in ASTM F3411-22 §A.3.
Sends two frame types:
  1. NAN Sync Beacon — announces the NAN cluster
  2. NAN Service Discovery Frame (Public Action Frame) — carries the Remote ID
     Message Pack payload

Uses nl80211 netlink interface to inject management frames, as NAN frames
are Public Action frames that require kernel-level injection via nl80211
rather than simple raw socket send.

References:
  - OpenDroneID Core C library: wifi/sender/main.c
  - Wi-Fi Alliance NAN Specification v2.0
  - IEEE 802.11-2016 Public Action Frame format
"""

import logging
import os
import random
import socket
import struct
import subprocess
import threading
import time
from typing import Dict, List, Optional, Tuple

from drone_rid_spoofer.messages import build_message_pack
from drone_rid_spoofer.state import DroneState
from drone_rid_spoofer.transport.base import TransportBackend

logger = logging.getLogger(__name__)

# ── 802.11 / NAN constants ──────────────────────────────────────────────

# NAN OUI (Wi-Fi Alliance)
NAN_OUI = b'\x50\x6f\x9a'
NAN_OUI_TYPE = 0x13

# NAN Service Discovery multicast address (per Wi-Fi Alliance NAN spec)
NAN_MULTICAST_ADDR = '50:6f:9a:01:00:00'

# IEEE 802.11 frame types
WLAN_TYPE_MGMT = 0x00
WLAN_SUBTYPE_BEACON = 0x80
WLAN_SUBTYPE_ACTION = 0xD0

# Public Action Frame category
ACTION_CATEGORY_PUBLIC = 0x04
# NAN Action Code (defined by Wi-Fi Alliance)
ACTION_CODE_NAN = 0x13

# NAN Attribute IDs
NAN_ATTR_MASTER_INDICATION = 0x00
NAN_ATTR_CLUSTER = 0x01
NAN_ATTR_SERVICE_ID_LIST = 0x02
NAN_ATTR_SERVICE_DESCRIPTOR = 0x03
NAN_ATTR_SERVICE_DESCRIPTOR_EXT = 0x0E  # NAN 2.0 extended
NAN_ATTR_VENDOR_SPECIFIC = 0xDD

# ODID Service ID (first 6 bytes of SHA-256("org.opendroneid.remoteid"))
ODID_SERVICE_ID = bytes.fromhex('0a556e5a9b6e')

# nl80211 constants (Linux netlink)
NL80211_CMD_FRAME = 59
NL80211_ATTR_IFINDEX = 3
NL80211_ATTR_FRAME = 33
NL80211_ATTR_DONT_WAIT_FOR_ACK = 52

# netlink constants
NETLINK_GENERIC = 16
NLM_F_REQUEST = 1
NLM_F_ACK = 4

# NAN Cluster ID range (random 8-byte value, re-randomized periodically)
CLUSTER_ID_BYTES = 8

# Sync Beacon interval (every ~500ms per Wi-Fi Alliance NAN spec)
SYNC_BEACON_INTERVAL_S = 0.512  # ≈ 500 TU


def _mac_to_bytes(mac: str) -> bytes:
    """Convert 'aa:bb:cc:dd:ee:ff' string to 6 bytes."""
    return bytes(int(p, 16) for p in mac.split(':'))


def _bytes_to_mac(b: bytes) -> str:
    """Convert 6 bytes to 'aa:bb:cc:dd:ee:ff' string."""
    return ':'.join(f'{x:02x}' for x in b)


def _generate_cluster_id() -> bytes:
    """Generate a random NAN Cluster ID (8 bytes)."""
    return bytes(random.randint(0, 255) for _ in range(CLUSTER_ID_BYTES))


# ── 802.11 frame construction ───────────────────────────────────────────

def _build_radiotap_header() -> bytes:
    """Build a minimal Radiotap header for frame injection.

    Flags: 0x00 (no FCS at end, no bad FCS flag).
    Rate: not set (controller will pick).
    Channel: not set (controller already locked to channel).
    """
    # Radiotap header:
    #   version(1) + pad(1) + length(2) + present_flags(4)
    # We use 0 present flags — just the minimum 8-byte header.
    return struct.pack('<BBHI', 0x00, 0x00, 0x08, 0x00000000)


def _build_80211_mgmt_header(subtype: int, da: str, sa: str, bssid: str,
                              seq_num: int) -> bytes:
    """Build an 802.11 management frame header (24 bytes).

    Args:
        subtype: Frame subtype (0x80=Beacon, 0xD0=Action).
        da: Destination MAC.
        sa: Source MAC.
        bssid: BSSID.
        seq_num: 12-bit sequence number (0-4095).
    """
    frame_control = struct.pack('<H', WLAN_TYPE_MGMT | subtype)
    duration = struct.pack('<H', 0)
    da_bytes = _mac_to_bytes(da)
    sa_bytes = _mac_to_bytes(sa)
    bssid_bytes = _mac_to_bytes(bssid)
    seq_ctrl = struct.pack('<H', (seq_num << 4) & 0xFFF0)
    return frame_control + duration + da_bytes + sa_bytes + bssid_bytes + seq_ctrl


# ── NAN Sync Beacon construction ────────────────────────────────────────

def _build_nan_sync_beacon(mac: str, cluster_id: bytes, seq_num: int) -> bytes:
    """Build a NAN Sync Beacon frame.

    NAN Sync Beacon uses a standard Beacon frame (subtype 8) with a
    Vendor-Specific IE containing NAN attributes:
      - Cluster Attribute (ID=0x01): NAN Cluster ID
      - Master Indication Attribute (ID=0x00): Master preference

    Frame structure:
      Radiotap + 802.11 Beacon Header + NAN Vendor IE

    Args:
        mac: Source MAC address.
        cluster_id: 8-byte NAN Cluster ID.
        seq_num: 12-bit sequence number.

    Returns:
        Complete frame bytes ready for injection.
    """
    radiotap = _build_radiotap_header()

    # 802.11 Beacon header
    mgmt_header = _build_80211_mgmt_header(
        subtype=WLAN_SUBTYPE_BEACON,
        da='ff:ff:ff:ff:ff:ff',
        sa=mac,
        bssid=mac,
        seq_num=seq_num,
    )

    # Beacon frame body: timestamp(8) + beacon_interval(2) + capability(2)
    timestamp = struct.pack('<Q', int(time.time() * 1000000) % (2**64))
    beacon_interval = struct.pack('<H', int(SYNC_BEACON_INTERVAL_S * 1024))  # TU
    capability = struct.pack('<H', 0x0000)  # No ESS/IBSS

    # Build NAN Vendor-Specific IE content
    # NAN attributes:
    #   1. Master Indication (ID=0x00, length=5)
    #   2. Cluster (ID=0x01, length=8)
    master_indication = bytes([
        NAN_ATTR_MASTER_INDICATION,  # Attribute ID
        0x05,                         # Length
        0x00, 0x00,                   # Master Preference (0 = non-master)
        0x00, 0x00, 0x00,             # Reserved
    ])
    cluster_attr = bytes([
        NAN_ATTR_CLUSTER,             # Attribute ID
        CLUSTER_ID_BYTES,             # Length (8)
    ]) + cluster_id

    nan_attributes = master_indication + cluster_attr
    vendor_ie_content = NAN_OUI + bytes([NAN_OUI_TYPE]) + nan_attributes

    # Vendor-Specific IE: ID=221, length, OUI+type+attributes
    vendor_ie = bytes([221, len(vendor_ie_content)]) + vendor_ie_content

    frame_body = timestamp + beacon_interval + capability + vendor_ie
    return radiotap + mgmt_header + frame_body


# ── NAN Service Discovery Frame construction ─────────────────────────────

def _build_nan_sdf(mac: str, message_pack: bytes, cluster_id: bytes,
                   seq_num: int, service_id: bytes = ODID_SERVICE_ID) -> bytes:
    """Build a NAN Service Discovery Frame (Public Action Frame).

    Carries the ODID Remote ID Message Pack as a NAN Service Descriptor
    Attribute within a Public Action frame.

    Frame structure:
      Radiotap + 802.11 Action Header + Public Action Body + NAN Attributes

    NAN Attributes carried:
      1. Service ID List (ID=0x02): identifies ODID service
      2. Service Descriptor (ID=0x03): carries the Message Pack payload

    Args:
        mac: Source MAC address.
        message_pack: Pre-built Message Pack bytes.
        cluster_id: 8-byte NAN Cluster ID.
        seq_num: 12-bit sequence number.
        service_id: 6-byte ODID Service ID.

    Returns:
        Complete frame bytes ready for injection.
    """
    radiotap = _build_radiotap_header()

    # 802.11 Action frame header
    # DA = NAN multicast address, SA = our MAC, BSSID = NAN multicast
    mgmt_header = _build_80211_mgmt_header(
        subtype=WLAN_SUBTYPE_ACTION,
        da=NAN_MULTICAST_ADDR,
        sa=mac,
        bssid=NAN_MULTICAST_ADDR,
        seq_num=seq_num,
    )

    # Public Action frame body:
    #   Category (1) + Action Code (1) + OUI (3) + OUI Type (1)
    action_body = bytes([
        ACTION_CATEGORY_PUBLIC,   # Category: Public Action
        ACTION_CODE_NAN,           # Action Code: NAN
    ]) + NAN_OUI + bytes([NAN_OUI_TYPE])

    # NAN Attribute: Service ID List
    # Contains the 6-byte ODID service ID
    service_id_attr = bytes([
        NAN_ATTR_SERVICE_ID_LIST,  # Attribute ID
        0x06,                       # Length (6)
    ]) + service_id

    # NAN Attribute: Service Descriptor
    # Carries the Message Pack as opaque service-specific data.
    # The Message Pack payload is the service_info field.
    service_descriptor = bytes([
        NAN_ATTR_SERVICE_DESCRIPTOR,  # Attribute ID
    ])
    # Service Descriptor body:
    #   Instance ID (1) + Requestor Instance ID (1) + Service Control (1)
    #   + Binding Bitmap (optional, 0 here) + Matching Filter Length (1)
    #   + Matching Filter (0 here) + Service Response Filter Length (1)
    #   + Service Response Filter (0 here) + Service Info Length (1)
    #   + Service Info (Message Pack)
    sd_body = bytes([
        0x01,  # Instance ID
        0x00,  # Requestor Instance ID (0 = unsolicited publish)
        0x00,  # Service Control (0 = publish, no follow-up)
        0x00,  # Matching Filter Length (no matching filter)
        # No matching filter bytes
        0x00,  # Service Response Filter Length
        # No service response filter bytes
        len(message_pack) & 0xFF,  # Service Info Length
    ]) + message_pack

    # Pack Service Descriptor attribute: ID(1) + Length(2 LE) + body
    sd_len = len(sd_body)
    sd_attr = service_descriptor + struct.pack('<H', sd_len) + sd_body

    nan_attrs = service_id_attr + sd_attr

    return radiotap + mgmt_header + action_body + nan_attrs


# ── nl80211 netlink frame injection ─────────────────────────────────────

class _Nl80211Injector:
    """Inject 802.11 management frames via nl80211 netlink on Linux.

    Uses the NL80211_CMD_FRAME command to inject raw frames, which is
    required for Public Action frames (including NAN SDF) because they
    are not simple data frames that can go through a monitor interface.
    """

    def __init__(self, ifname: str):
        self.ifname = ifname
        self.ifindex = self._get_ifindex(ifname)
        self._sock: Optional[socket.socket] = None
        self._nl80211_family_id: Optional[int] = None
        self._seq = 0

    def _get_ifindex(self, ifname: str) -> int:
        """Get interface index via if_nametoindex equivalent."""
        import fcntl
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            ifreq = struct.pack('256s', ifname[:15].encode())
            result = fcntl.ioctl(sock, 0x8933, ifreq)  # SIOCGIFINDEX
            return struct.unpack('I', result[16:20])[0]
        finally:
            sock.close()

    def _open(self) -> None:
        """Open nl80211 netlink socket and resolve family ID."""
        if self._sock is not None:
            return
        try:
            self._sock = socket.socket(
                socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_GENERIC
            )
            self._sock.bind((0, 0))
            self._nl80211_family_id = self._resolve_family("nl80211")
        except (OSError, PermissionError) as e:
            raise RuntimeError(
                f"Failed to open nl80211 socket. Requires root and Linux. "
                f"Error: {e}"
            ) from e

    def _resolve_family(self, name: str) -> int:
        """Resolve a generic netlink family name to a family ID."""
        # CTRL_CMD_GETFAMILY request
        msg = self._build_genl_msg(
            cmd=3,  # CTRL_CMD_GETFAMILY
            family_id=0x10,  # GENL_ID_CTRL
            flags=NLM_F_REQUEST,
            attrs={2: name.encode() + b'\x00'},  # CTRL_ATTR_FAMILY_NAME
        )
        self._sock.send(msg)
        resp = self._sock.recv(4096)

        # Parse response for CTRL_ATTR_FAMILY_ID (attr 1)
        # Response has: nlmsghdr(16) + genlmsghdr(4) + attrs
        offset = 20  # skip nlmsghdr(16) + genlmsghdr(4)
        while offset + 4 <= len(resp):
            attr_len, attr_type = struct.unpack('<HH', resp[offset:offset+4])
            if attr_type == 1:  # CTRL_ATTR_FAMILY_ID
                return struct.unpack('<H', resp[offset+4:offset+6])[0]
            # Advance to next attribute (rounded up to 4-byte boundary)
            offset += (attr_len + 3) & ~3
        raise RuntimeError("Failed to resolve nl80211 family ID")

    def _build_genl_msg(self, cmd: int, family_id: int, flags: int,
                        attrs: Dict[int, bytes]) -> bytes:
        """Build a generic netlink message."""
        self._seq += 1
        # nlmsghdr: length(4) + type(2) + flags(2) + seq(4) + pid(4)
        # genlmsghdr: cmd(1) + version(1) + reserved(2)
        genl_hdr = struct.pack('BBH', cmd, 0, 0)

        # Build attribute payload
        attr_payload = b''
        for attr_type, attr_data in attrs.items():
            # Align attribute data to 4 bytes
            padded = attr_data
            pad = (4 - len(padded) % 4) % 4
            padded += b'\x00' * pad
            attr_len = 4 + len(attr_data)  # nla header is 4 bytes
            attr_payload += struct.pack('<HH', attr_len, attr_type) + attr_data
            if pad:
                attr_payload += b'\x00' * pad

        payload = genl_hdr + attr_payload
        nlmsg_len = 16 + len(payload)  # nlmsghdr is 16 bytes
        nlmsg = struct.pack('<IHHII', nlmsg_len, family_id, flags,
                            self._seq, 0) + payload
        return nlmsg

    def send_frame(self, frame: bytes) -> None:
        """Inject a raw 802.11 frame via nl80211 NL80211_CMD_FRAME."""
        self._open()
        msg = self._build_genl_msg(
            cmd=NL80211_CMD_FRAME,
            family_id=self._nl80211_family_id,
            flags=NLM_F_REQUEST | NLM_F_ACK,
            attrs={
                NL80211_ATTR_IFINDEX: struct.pack('<I', self.ifindex),
                NL80211_ATTR_FRAME: frame,
                NL80211_ATTR_DONT_WAIT_FOR_ACK: b'',  # flag, no data
            },
        )
        try:
            self._sock.send(msg)
            # Drain ACK to avoid kernel buffer buildup
            self._sock.settimeout(0.1)
            try:
                self._sock.recv(4096)
            except socket.timeout:
                pass
        except OSError as e:
            logger.debug(f"nl80211 send error: {e}")

    def close(self) -> None:
        if self._sock:
            self._sock.close()
            self._sock = None


# ── Fallback: subprocess-based injection (iw mgmt send) ─────────────────

class _IwInjector:
    """Fallback frame injector using 'iw dev <iface> mgmt send' command.

    Some kernels / nl80211 versions require frames to be sent through
    the iw command rather than direct netlink. This is simpler but
    has higher per-frame overhead.
    """

    def __init__(self, ifname: str):
        self.ifname = ifname

    def send_frame(self, frame: bytes) -> None:
        """Inject frame via iw mgmt send."""
        frame_hex = frame.hex()
        try:
            subprocess.run(
                ['iw', 'dev', self.ifname, 'mgmt', 'send', frame_hex],
                capture_output=True, timeout=1.0, check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.debug(f"iw mgmt send error: {e}")

    def close(self) -> None:
        pass


# ── NanBackend ───────────────────────────────────────────────────────────

class NanBackend(TransportBackend):
    """Wi-Fi NAN transport backend for ASTM F3411-22 Remote ID.

    Sends NAN Sync Beacons and NAN Service Discovery Frames containing
    the ODID Message Pack. Operates on a dedicated channel (default: 6).

    Multi-drone support: all drones share the same NAN cluster. Each
    drone gets its own Sync Beacon + SDF pair per transmission cycle.
    Drones are time-multiplexed within each cycle.

    Requirements:
      - Linux with nl80211-capable Wi-Fi driver
      - Root privileges
      - Interface in monitor mode or managed mode with frame injection support
    """

    # Maximum message pack size (header 3 + 9 messages * 25 bytes)
    MAX_PACK_SIZE = 3 + 9 * 25  # = 228

    def __init__(self, interface: str, channel: int = 6,
                 sync_beacon_interval: float = SYNC_BEACON_INTERVAL_S,
                 protocol_version: int = 2):
        self.interface = interface
        self.channel = channel
        self.sync_beacon_interval = sync_beacon_interval
        self.protocol_version = protocol_version

        # NAN cluster state
        self._cluster_id = _generate_cluster_id()
        self._seq_num = 0
        self._seq_lock = threading.Lock()

        # Per-drone payload cache
        self._payloads: Dict[bytes, bytes] = {}
        self._payloads_lock = threading.Lock()

        # nl80211 injector
        self._injector: Optional[_Nl80211Injector] = None
        try:
            self._injector = _Nl80211Injector(interface)
            logger.info("NAN backend: using nl80211 netlink injection")
        except RuntimeError as e:
            logger.warning(f"nl80211 unavailable ({e}), falling back to iw mgmt send")
            self._injector = _IwInjector(interface)

        # Lock interface to target channel
        self._lock_channel()

        # Sync Beacon thread
        self._running = True
        self._sync_thread = threading.Thread(target=self._sync_beacon_loop,
                                             daemon=True)
        self._sync_thread.start()
        logger.info(f"NAN backend started on {interface} channel {channel}, "
                     f"sync beacon interval {sync_beacon_interval*1000:.0f}ms")

    def _lock_channel(self) -> None:
        """Lock the physical interface to the target channel."""
        try:
            subprocess.run(
                ['iw', 'dev', self.interface, 'set', 'channel', str(self.channel)],
                capture_output=True, check=True,
            )
            logger.info(f"NAN interface {self.interface} locked to channel {self.channel}")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.warning(f"Could not lock channel on {self.interface}: {e}")

    def _next_seq(self) -> int:
        """Get next 12-bit sequence number, thread-safe."""
        with self._seq_lock:
            num = self._seq_num
            self._seq_num = (self._seq_num + 1) % 4096
            return num

    def _sync_beacon_loop(self) -> None:
        """Periodically send NAN Sync Beacons to maintain cluster presence."""
        # Wait a short time before first beacon to let initialization complete
        time.sleep(0.1)
        mac = self._get_interface_mac()
        while self._running:
            seq = self._next_seq()
            beacon = _build_nan_sync_beacon(mac, self._cluster_id, seq)
            self._injector.send_frame(beacon)
            time.sleep(self.sync_beacon_interval)

    def _get_interface_mac(self) -> str:
        """Get the MAC address of the Wi-Fi interface."""
        try:
            import fcntl
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            ifreq = struct.pack('256s', self.interface[:15].encode())
            result = fcntl.ioctl(sock, 0x8927, ifreq)  # SIOCGIFHWADDR
            mac_bytes = result[18:24]
            return _bytes_to_mac(mac_bytes)
        except Exception:
            # Fallback: generate a valid LAA MAC
            from drone_rid_spoofer.helpers import generate_wifi_mac
            return generate_wifi_mac()

    def send_messages(self, drone: DroneState, messages: List[bytes]) -> None:
        """Send ODID messages as a NAN Service Discovery Frame.

        Builds a Message Pack from all messages, then sends it inside
        a NAN SDF (Public Action Frame) with the drone's MAC as source.

        Args:
            drone: The drone state (provides MAC, serial, etc.).
            messages: List of 25-byte ASTM message payloads.
        """
        # Build Message Pack
        pack = build_message_pack(messages, proto=self.protocol_version)

        if len(pack) > self.MAX_PACK_SIZE:
            logger.warning(
                f"Message pack too large ({len(pack)} > {self.MAX_PACK_SIZE}), "
                f"truncating to first 9 messages"
            )
            # Rebuild with only first 9 messages
            pack = build_message_pack(
                messages[:9], proto=self.protocol_version
            )

        seq = self._next_seq()
        mac = drone.mac_address

        # Build and send the NAN Service Discovery Frame
        sdf = _build_nan_sdf(mac, pack, self._cluster_id, seq)
        self._injector.send_frame(sdf)

        logger.debug(
            f"NAN SDF sent for {drone.serial.decode('ascii', errors='replace')} "
            f"(MAC {mac}, seq {seq}, pack {len(pack)} bytes)"
        )

    def close(self) -> None:
        """Stop sync beacon thread and release resources."""
        self._running = False
        if self._sync_thread.is_alive():
            self._sync_thread.join(timeout=2.0)
        if self._injector:
            self._injector.close()
        logger.info("NAN backend closed")
