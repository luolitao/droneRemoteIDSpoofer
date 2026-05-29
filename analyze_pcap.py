"""Analyze the opendroneid WiFi sample pcap to verify GB payload format."""
from scapy.all import rdpcap, Dot11Elt, Dot11Beacon
import struct

ODID_OUI = bytes.fromhex('fa0bbc')

packets = rdpcap('odid_wifi_sample.pcap')
print(f'Total packets: {len(packets)}')

found = 0
for i, pkt in enumerate(packets):
    if Dot11Beacon not in pkt:
        continue
    
    vendor_ie = None
    elt = pkt
    while hasattr(elt, 'payload'):
        elt = elt.payload
        if isinstance(elt, Dot11Elt) and elt.ID == 221:
            vendor_ie = elt
            break
    
    if not vendor_ie:
        continue
    
    info = bytes(vendor_ie.info)
    if info[:3] != ODID_OUI:
        continue
    
    found += 1
    print(f'\n===== Packet {i} (ODID beacon #{found}) =====')
    print(f'  OUI: {info[:3].hex()}  oui_type: 0x{info[3]:02x}')
    print(f'  Full info hex ({len(info)} bytes): {info.hex()}')
    
    payload = info[4:]
    print(f'  Payload hex ({len(payload)} bytes): {payload.hex()}')
    
    # Parse according to ASTM F3411 Message Pack format
    # Format: [MsgCounter(1)][Reserved(3)][MsgPackSize(1)][SingleMsgSize(1)][Messages...]
    if len(payload) < 6:
        print('  Payload too short')
        continue
    
    msg_counter = payload[0]
    print(f'  MsgCounter: {msg_counter}')
    print(f'  Reserved: {payload[1:4].hex()}')
    msg_pack_size = payload[4]
    single_msg_size = payload[5]
    print(f'  MsgPackSize: {msg_pack_size}, SingleMsgSize: {single_msg_size}')
    
    messages = payload[6:]
    expected_total = msg_pack_size * single_msg_size
    print(f'  Messages bytes: {len(messages)}, expected: {expected_total}')
    
    if expected_total > len(messages):
        print(f'  WARNING: expected {expected_total} but got {len(messages)}')
    
    type_names = {0: 'BasicID', 1: 'Location', 2: 'Auth', 3: 'SelfID', 4: 'System', 5: 'OperatorID'}
    
    for m in range(msg_pack_size):
        start = m * single_msg_size
        end = start + single_msg_size
        if end > len(messages):
            print(f'  Message {m}: TRUNCATED (need {end}, have {len(messages)})')
            break
        
        msg = messages[start:end]
        if len(msg) < 1:
            continue
        msg_type = (msg[0] >> 4) & 0x0F
        proto_ver = msg[0] & 0x0F
        tname = type_names.get(msg_type, f'Unknown({msg_type})')
        
        print(f'\n  --- Msg {m}: {tname} v{proto_ver} [{len(msg)}B] ---')
        print(f'  Hex: {msg.hex()}')
        
        if len(msg) < 25:
            print(f'  Short msg ({len(msg)}B), skipping detailed parse')
            continue
        
        if msg_type == 1:  # Location
            status = (msg[1] >> 4) & 0x0F
            reserved1 = (msg[1] >> 3) & 0x01
            height_type = (msg[1] >> 2) & 0x01
            ew_dir = (msg[1] >> 1) & 0x01
            speed_mult = msg[1] & 0x01
            direction = msg[2]
            speed_h = msg[3]
            speed_v = struct.unpack('<b', msg[4:5])[0]
            lat = struct.unpack('<i', msg[5:9])[0]
            lng = struct.unpack('<i', msg[9:13])[0]
            alt_baro = struct.unpack('<H', msg[13:15])[0]
            alt_geo = struct.unpack('<H', msg[15:17])[0]
            height = struct.unpack('<H', msg[17:19])[0]
            
            actual_dir = direction + (180 if ew_dir else 0)
            actual_speed = speed_h * (0.75 if speed_mult else 0.25)
            actual_vspeed = speed_v * 0.5
            
            print(f'  Byte1: St={status} HtTyp={height_type} EW={ew_dir} SpdM={speed_mult}')
            print(f'  Dir={direction}°(actual={actual_dir}°) SpdH={speed_h}({actual_speed:.1f}m/s) SpdV={speed_v}({actual_vspeed:.1f}m/s)')
            print(f'  Pos: ({lat/1e7:.7f}, {lng/1e7:.7f})')
            print(f'  Alt: Baro={alt_baro*0.5-1000:.1f}m Geo={alt_geo*0.5-1000:.1f}m Hgt={height*0.5-1000:.1f}m')
        
        elif msg_type == 0:
            id_type = (msg[1] >> 4) & 0x0F
            ua_type = msg[1] & 0x0F
            uasid = msg[2:22].split(b'\x00')[0].decode('ascii', errors='replace')
            print(f'  IDType={id_type} UAType={ua_type} UASID="{uasid}"')
        
        elif msg_type == 3:
            desc = msg[2:25].split(b'\x00')[0].decode('ascii', errors='replace')
            print(f'  DescType={msg[1]} Desc="{desc}"')
    
    if found >= 2:
        break
