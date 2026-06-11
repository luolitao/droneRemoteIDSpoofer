from typing import List
from drone_rid_spoofer.protocols.base import BaseProtocolPacker
from drone_rid_spoofer.core.encoders import MsgType

class GB42590ProtocolPacker(BaseProtocolPacker):
    """中国国标 (GB 42590 / GB 46750) 专用打包器"""
    
    def __init__(self, proto_version: int = 2):
        self.proto_version = proto_version
        self._gb_send_counter = 0  # 独立维护国标序列号计数

    def pack(self, sub_messages: List[bytes]) -> bytes:
        # 1. 组装标准 0xF 消息体
        pack_type_ver = (MsgType.PACK << 4) | (self.proto_version & 0x0F)
        msg_size = 0x19
        msg_count = len(sub_messages) & 0xFF
        pack_header = bytes([pack_type_ver, msg_size, msg_count])
        
        # 核心区别：国标特殊的双重计数外壳或专用前缀
        # 此处根据国标检验仪的具体校验要求，填充国标专属外壳
        gb_header = bytes([0x0D, self._gb_send_counter]) 
        self._gb_send_counter = (self._gb_send_counter + 1) & 0xFF
        
        return gb_header + pack_header + b''.join(sub_messages)