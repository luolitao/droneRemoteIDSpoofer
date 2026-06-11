from drone_rid_spoofer.state import DroneState
from drone_rid_spoofer.transport.wifi import WifiTransport
from drone_rid_spoofer.protocols.astm_protocol import ASTMProtocolPacker
from drone_rid_spoofer.protocols.gb42590_protocol import GB42590ProtocolPacker

if __name__ == "__main__":
    # 初始化无人机虚拟状态
    my_drone = DroneState(
    serial=b"CN-GB-123456", 
    lat=399087123,                     # 无人机纬度 (乘以10^7)
    lng=116397456,                     # 无人机经度 (乘以10^7)
    pilot_location=[399087123, 116397456], # 【补齐】飞手经纬度，这里假设飞手和假飞机在同一个点
    mac_address="60:60:1f:aa:bb:cc",        # 【补齐】伪造的 Wi-Fi MAC 地址
    ble_address="60:60:1f:aa:bb:cd"         # 【补齐】伪造的蓝牙 MAC 地址
)
    
    # === 自由切换开关 ===
    USE_CHINA_GB = True 
    
    if USE_CHINA_GB:
        # 使用国标打包策略
        packer = GB42590ProtocolPacker(proto_version=2)
    else:
        # 使用国际标准打包策略
        packer = ASTMProtocolPacker(proto_version=2)
        
    # 将选择好的策略注入 Wi-Fi 传输层
    transport = WifiTransport(interface="wlan1", packer=packer)
    
    print(f"远程 ID 欺骗器启动，当前协议模式: {'中国国标 GB42590' if USE_CHINA_GB else '国际标 ASTM'}")
    transport.start(my_drone)