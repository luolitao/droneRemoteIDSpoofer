import struct
import time

class ASTMProtocolPacker:
    """
    ASTM F3411-22a / OpenDroneID 国际标准远程识别协议打包器
    负责将无人机动态遥测数据转换为符合国际标准的 25 字节模块化消息大包 (Message Pack)
    """
    def __init__(self):
        pass

    def encode_message_pack(self, uas_id, lat, lng, alt, height, heading=90.0, speed=12.5):
        """
        核心打包方法：组装 Basic ID, Location, 和 System 消息
        """
        # 1. 构造 Basic ID 消息 (Type 0x00, 25 字节)
        basic_id_header = b"\x00\x12"  # ID Type: 1 (Serial), UA Type: 2 (Quadcopter)
        uas_id_bytes = uas_id.encode('ascii')[:20].ljust(20, b'\x00')
        basic_id_msg = basic_id_header + uas_id_bytes + b"\x00\x00\x00"

        # 2. 构造 Location 消息 (Type 0x10, 25 字节)
        status_byte = 0x20  # In Flight
        heading_enc = int(heading / 1.5) & 0xFF
        speed_enc = int(speed / 0.25) & 0xFF
        lat_enc = int(lat * 1e7)
        lng_enc = int(lng * 1e7)
        alt_press_enc = int((alt + 1000) / 0.5) & 0xFFFF
        alt_geo_enc = int((alt + 1000) / 0.5) & 0xFFFF
        height_enc = int((height + 1000) / 0.5) & 0xFFFF
        
        now = time.time()
        timestamp_enc = int((now % 3600) * 10) & 0xFFFF
        
        location_msg = struct.pack(
            "<BBBBbiiHHHBBHBB",
            0x10, status_byte, heading_enc, speed_enc, 0,
            lat_enc, lng_enc, alt_press_enc, alt_geo_enc, height_enc,
            0x03, 0x03, timestamp_enc, 0x01, 0x00
        )

        # 3. 构造 System 消息 (Type 0x40, 25 字节)
        system_base = struct.pack(
            "<BBiiHHHHB",
            0x40, 0x01, lat_enc, lng_enc, 1, 0, alt_geo_enc, 0x0000, 0x00
        )
        system_msg = system_base + b"\x00" * 6

        # 4. 组装 Message Pack 大包头 (Type 0xF0, 3 字节)
        pack_header = b"\xf0\x19\x03"

        return pack_header + basic_id_msg + location_msg + system_msg

    def pack(self, *args, **kwargs):
        """兼容旧版别名方法"""
        return self.encode_message_pack(*args, **kwargs)

# 💡 别名导出：无论主脚本 import 哪个名字都能识别
ASTMProtocol = ASTMProtocolPacker