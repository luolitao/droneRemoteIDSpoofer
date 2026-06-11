import threading
import time
from typing import List
from scapy.all import Dot11, Dot11Beacon, Dot11Elt, RadioTap, sendp
from drone_rid_spoofer.state import DroneState
from drone_rid_spoofer.protocols.base import BaseProtocolPacker
from drone_rid_spoofer.core import encoders

class WifiTransport:
    """通用的 Wi-Fi 信标注入传输层"""
    
    def __init__(self, interface: str, packer: BaseProtocolPacker, oui: bytes = b'\xfa\x0b\xbc'):
        self.interface = interface
        self.packer = packer  # 注入对应的协议打包策略（ASTM 或 GB42590）
        self.oui = oui        # OpenDroneID 厂商组织标识
        self.is_running = False
        self._thread = None

    def _transmit_loop(self, drone: DroneState):
        while self.is_running:
            # 1. 调用底层 core 生成 25 字节单条弹药
            sub_msgs = [
                encoders.encode_basic_id(drone.serial),
                encoders.encode_location(drone)
            ]
            
            # 2. 多态调用：packer 会自动根据自己的策略打包（这就是解耦的精髓）
            vendor_data = self.packer.pack(sub_msgs)
            
            # 3. 塞进 Scapy 的 221 号 Vendor IE 元素发波
            ie_vendor = Dot11Elt(ID=221, info=self.oui + vendor_data)
            
            # 组装物理层基础 Beacon 帧（此处简化了 SSID 和 Rates 拼接逻辑）
            packet = RadioTap() / Dot11(type=0, subtype=8) / Dot11Beacon() / ie_vendor
            sendp(packet, iface=self.interface, verbose=False)
            
            time.sleep(0.1) # 约 100ms 发送一次

    def start(self, drone: DroneState):
        self.is_running = True
        self._thread = threading.Thread(target=self._transmit_loop, args=(drone,))
        self._thread.start()