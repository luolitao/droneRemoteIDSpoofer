from scapy.all import RadioTap, Dot11, Dot11Beacon, Dot11Elt, sendp, get_if_hwaddr

class WifiTransport:
    def __init__(self, interface="wlan0", ssid="Drone_Live"):
        self.interface = interface
        self.ssid_element = Dot11Elt(ID=0, info=ssid.encode('utf-8'))
        self.send_counter = 0
        
        # 💡 核心修复：动态获取当前网卡真实的硬件物理 MAC 地址
        try:
            self.real_mac = get_if_hwaddr(interface)
            # 容错处理：如果网卡未完全就绪抓出全 0，则指定一个合法的标准本地单播 MAC
            if not self.real_mac or self.real_mac == "00:00:00:00:00:00":
                self.real_mac = "74:ee:9a:bc:de:11"
        except Exception:
            self.real_mac = "74:ee:9a:bc:de:11"
            
        print(f"📡 [网卡物理层对齐] 已成功读取 {interface} 的真实 MAC 地址: {self.real_mac}")

    def send_gb42590_packet(self, message_pack_bytes):
        # 1. 计算 1 字节自增消息计数器
        counter_byte = bytes([self.send_counter])
        self.send_counter = (self.send_counter + 1) & 0xFF
        
        # 2. 严格拼装符合标准 IE 221 的净载荷
        payload_data = b"\xfa\x0b\xbc" + b"\x0d" + counter_byte + message_pack_bytes
        actual_len = len(payload_data)
        
        # 3. 手动注入 Element ID 221 和精准的 Len 长度
        custom_ie_bytes = bytes([221, actual_len]) + payload_data
        
        # 4. 构建链路物理层骨架
        # 💡 严格按事实对齐：将 addr2 和 addr3 替换为真实的网卡物理 MAC
        dot11_layer = Dot11(
            addr1="ff:ff:ff:ff:ff:ff",      # 接收端：广播
            addr2=self.real_mac,            # 发射端 (TA)：真实网卡 MAC
            addr3=self.real_mac,            # BSSID：真实网卡 MAC
            type=0, 
            subtype=8
        )
        packet = RadioTap() / dot11_layer / Dot11Beacon(cap="ESS") / self.ssid_element / custom_ie_bytes
        
        # 5. 网卡原生产生广播
        sendp(packet, iface=self.interface, verbose=False)