import struct
import time

class GB42590Protocol:
    """
    GB 42590-2023 中国国家标准民用无人驾驶航空器远程识别协议打包器
    结构与国际标准对齐，但采用符合国标规范的报头标识及特定的类型字段
    """
    def __init__(self):
        pass

    def encode_message_pack(self, uas_id, lat, lng, alt, height, heading=90.0, speed=12.5):
        """
        核心打包方法：组装符合国标要求的 ID、位置与系统数据大包
        """
        # 1. 构造 Basic ID 消息 (Type 0x00, 25 字节)
        basic_id_header = b"\x00\x22"  # ID Type: 2 (民用无人机唯一产品识别码 UIN)
        uas_id_bytes = uas_id.encode('ascii')[:20].ljust(20, b'\x00')
        basic_id_msg = basic_id_header + uas_id_bytes + b"\x00\x00\x00"

        # 2. 构造 Location 消息 (Type 0x10, 25 字节)
        status_byte = 0x20  # 飞行中
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

        # 4. 组装 Message Pack 大包头 (💡 国标大包头特有标识 Type 0xF1)
        pack_header = b"\xf1\x19\x03"

        return pack_header + basic_id_msg + location_msg + system_msg

    def pack(self, *args, **kwargs):
        """兼容旧版别名方法"""
        return self.encode_message_pack(*args, **kwargs)

# 💡 别名导出：确保 run_spoofer.py 里的 import GB42590Protocol 完美通过
GB42590ProtocolPacker = GB42590Protocol