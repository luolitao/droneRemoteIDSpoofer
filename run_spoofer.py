import argparse
import time
import random
from datetime import datetime

# 💡 修正导入：根据你项目原本的真实文件名导入协议器
# 请根据实际情况微调这里的类名（例如：GB42590Protocol, AstmProtocol / OpenDroneID）
from drone_rid_spoofer.protocols.gb42590_protocol import GB42590Protocol 
from drone_rid_spoofer.protocols.astm_protocol import ASTMProtocol  # 或者是 AstmProtocol，取决于类名
from drone_rid_spoofer.transport.wifi import WifiTransport

def print_audit_summary(protocol_name, counter, iface, lat, lng, alt, height, p_size):
    """通用状态审计看板"""
    print("\n" + "="*60)
    print(f"📊 [{protocol_name.upper()} 协议] 远程识别状态审计看板")
    print("-"*60)
    print(f"⏱️  当前系统时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🔢 累计发射包数: {counter:<10} | 📊 物理层净载荷: {p_size} 字节")
    print("-"*60)
    print(f"🛸 [UAV 动态航迹向量]")
    print(f"   ├─ 经纬坐标: [{lat:.7f}, {lng:.7f}]")
    if protocol_name.lower() == 'gb42590':
        print(f"   ├─ 气压/几何高度: {alt:.2f} m (国标编码值: {int((alt+1000)/0.5)})")
    else:
        print(f"   ├─ 气压/几何高度: {alt:.2f} m (ASTM直发值: {int(alt/0.5)})")
    print(f"   └─ 相对距地高度: {height:.2f} m")
    print(f"⚡ 传输状态: 🟢 物理层指针完全对齐 (Len 0x53) | 正在通过 {iface} 广发...")
    print("="*60 + "\n")

def main():
    parser = argparse.ArgumentParser(description="Drone Remote ID Spoofer - 协议与网卡切换控制台")
    
    # 协议切换参数
    parser.add_argument(
        '-p', '--protocol', 
        choices=['gb42590', 'astm'], 
        default='gb42590', 
        help='选择发射协议：gb42590 或 astm'
    )
    
    # 无线网卡接口参数
    parser.add_argument(
        '-i', '--interface', 
        default='wlan1', 
        help='指定处于 Monitor 模式的无线网卡接口'
    )
    
    # 自定义 SSID
    parser.add_argument(
        '-s', '--ssid', 
        default=None, 
        help='自定义 Wi-Fi 广播的 SSID 名称'
    )

    args = parser.parse_args()

    # 2. 动态路由到你原本的两个程序/协议器
    if args.protocol == 'gb42590':
        encoder = GB42590Protocol()  # 实例化你原本的国标程序
        default_ssid = "Drone_GB42590_Live"
        protocol_title = "GB42590 (中国国标)"
    else:
        encoder = ASTMProtocol()     # 实例化你原本的 ASTM 程序
        default_ssid = "Drone_ASTM_Standard"
        protocol_title = "ASTM F3411-22a"
        
    chosen_ssid = args.ssid if args.ssid else default_ssid

    # 3. 初始化物理传输层
    transport = WifiTransport(interface=args.interface, ssid=chosen_ssid)
    
    # 4. 初始化位置基准（广东区域）
    lat, lng = 23.1431123, 113.262756
    alt, height = 100.0, 50.0
    
    print("="*60)
    print(f"🚀 [状态机启动成功]")
    print(f"   ├─ 目标协议: {protocol_title}")
    print(f"   ├─ 物理网卡: {args.interface}")
    print(f"   └─ 广播 SSID: {chosen_ssid}")
    print("="*60)
    
    total_sent_packets = 0
    
    while True:
        # 5. 产生轻微飞行位移游走
        lat += random.uniform(-1e-5, 1e-5)
        lng += random.uniform(-1e-5, 1e-5)
        alt += random.uniform(-0.5, 0.5)
        height += random.uniform(-0.5, 0.5)
        
        # 6. 调用协议器原本的大包打包方法
        # 💡 注意：这里的方法名如果叫 encode_pack 或其他，请替换为你原本程序里的真实打包方法
        msg_pack = encoder.encode_message_pack(
            uas_id="CN-DRONE-123456",
            lat=lat, lng=lng, alt=alt, height=max(0, height),
            heading=90.0, speed=1.5
        )
        
        # 7. 传输层广发
        try:
            transport.send_gb42590_packet(msg_pack)
            total_sent_packets += 1
        except Exception as e:
            print(f"❌ [{args.interface}] 物理层发射失败: {e}")
            
        # 8. 定时总结看板
        if total_sent_packets % 50 == 0:
            payload_size = 3 + 1 + 1 + len(msg_pack) 
            print_audit_summary(args.protocol, total_sent_packets, args.interface, lat, lng, alt, height, payload_size)
            
        time.sleep(0.1)

if __name__ == "__main__":
    main()