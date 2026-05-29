"""Verify our encoding matches the opendroneid pcap format."""
from drone_rid_spoofer.state import DroneState
from drone_rid_spoofer.odid_encoding import build_message_pack, encode_basic_id, encode_location, encode_self_id

# Build our drone
drone = DroneState(
    serial=b'GB_TEST_00001',
    pilot_location=(231290000, 1132643000),
    lat=231292000, lng=1132645000,
    mac_address='02:00:00:00:00:01',
    ble_address='ed:e3:e0:3a:9e:c4',
    speed=8.0, vertical_speed=0.5,
    pressure_altitude=118.0, geodetic_altitude=120.0, height=60.0,
)

payload = build_message_pack(drone, 34)  # counter=34 like pcap
print(f"Our payload ({len(payload)} bytes):")
print(f"  {payload.hex()}")

# Parse our own payload to verify structure
print(f"\nParsing our payload:")
print(f"  counter: {payload[0]}")
print(f"  MsgPack header byte: 0x{payload[1]:02X} -> MsgType={payload[1]>>4}, ProtoVer={payload[1]&0x0F}")
print(f"  SingleMsgSize: {payload[2]}")
print(f"  MsgPackSize: {payload[3]}")

msg_count = payload[3]
msg_size = payload[2]
for i in range(msg_count):
    start = 4 + i * msg_size
    end = start + msg_size
    msg = payload[start:end]
    mt = msg[0] >> 4
    pv = msg[0] & 0x0F
    type_names = {0: 'BasicID', 1: 'Location', 2: 'Auth', 3: 'SelfID', 4: 'System', 5: 'OperatorID'}
    print(f"  Message {i}: {type_names.get(mt, f'Unknown({mt})')} v{pv} -> {msg.hex()}")

# Compare with pcap format
print("\n=== PCAP reference (Packet 2) ===")
pcap_payload = bytes.fromhex("22f0190150004742522d4f502d31323341424344000000000000000004")
print(f"  {pcap_payload.hex()}")
print(f"  counter: {pcap_payload[0]}")
print(f"  MsgPack header: 0x{pcap_payload[1]:02X} -> MsgType={pcap_payload[1]>>4}, ProtoVer={pcap_payload[1]&0x0F}")
print(f"  SingleMsgSize: {pcap_payload[2]}")
print(f"  MsgPackSize: {pcap_payload[3]}")
# PCAP has 1 message (OperatorID)
msg = pcap_payload[4:29]
mt = msg[0] >> 4
pv = msg[0] & 0x0F
print(f"  Message 0: {type_names.get(mt, f'Unknown({mt})')} v{pv} -> {msg.hex()}")
# Extract UASID
op_id = msg[2:22].split(b'\x00')[0].decode('ascii', errors='replace')
print(f"  Operator ID: '{op_id}'")

print(f"\n=== Format verification ===")
print(f"Our payload header structure matches pcap: {payload[1]>>4 == pcap_payload[1]>>4}")
print(f"Our SingleMsgSize matches: {payload[2] == 25}")
