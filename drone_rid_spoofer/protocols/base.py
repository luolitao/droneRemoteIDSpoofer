from abc import ABC, abstractmethod
from typing import List

class BaseProtocolPacker(ABC):
    """所有远程 ID 协议打包器的抽象基类"""
    
    @abstractmethod
    def pack(self, sub_messages: List[bytes]) -> bytes:
        """
        将多个 25 字节的基础子消息打包成对应协议规范的最终载荷。
        :param sub_messages: 25字节基础消息列表
        :return: 最终用于无线电发射的字节流 (Vendor Data)
        """
        pass