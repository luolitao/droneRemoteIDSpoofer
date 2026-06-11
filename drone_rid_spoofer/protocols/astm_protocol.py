from typing import List
from drone_rid_spoofer.protocols.base import BaseProtocolPacker
from drone_rid_spoofer.core.encoders import MsgType

class ASTMProtocolPacker(BaseProtocolPacker):
    """国际标准 (ASTM / OpenDroneID) 打包器"""
    
    def __init__(self, app_code: int = 0x0D, proto_version: int = 2):
        self.app_code = app_code
        self.proto_version = proto_version
        self._counter = 0

    def pack(self, sub_messages: List[bytes]) -> bytes:
        # 1. 组装标准 0xF Pack 头部
        pack_type_ver = (MsgType.PACK << 4) | (self.proto_version & 0x0F) # 0xF2
        msg_size = 0x19  # 每个子包固定 25 字节
        msg_count = len(sub_messages) & 0xFF
        
        pack_header = bytes([pack_type_ver, msg_size, msg_count])
        
        # 2. 拼接外壳 (AppCode + 帧计数器)
        transport_header = bytes([self.app_code, self._counter])
        self._counter = (self._counter + 1) & 0xFF
        
        # 3. 粘合所有子消息
        return transport_header + pack_header + b''.join(sub_messages)