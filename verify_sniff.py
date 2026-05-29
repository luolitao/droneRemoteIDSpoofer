"""Verify sniff_gb.py can decode our payload."""
from drone_rid_spoofer.odid_encoding import build_message_pack, decode_message_pack
from drone_rid_spoofer.state import DroneState

drone = DroneState(
    serial=b'GB_TEST_00001',
    pilot_location=(231290000, 1132643000),
    lat=231292000, lng=1132645000,
    mac_address='02:00:00:00:00:01',
    ble_address='ed:e3:e0:3a:9e:c4',
    speed=8.0, vertical_speed=0.5,
    pressure_altitude=118.0, geodetic_altitude=120.0, height=60.0,
)

payload = build_message_pack(drone, 0)
print(f"Payload: {len(payload)} bytes")

# Parse same way as sniff_gb: skip counter, then parse MessagePack header
msg_counter = payload[0]
msg_pack_data = payload[1:]

mp_header = msg_pack_data[0]
mp_msg_type = (mp_header >> 4) & 0x0F
mp_proto_ver = mp_header & 0x0F
single_msg_size = msg_pack_data[1]
msg_pack_size = msg_pack_data[2]

print(f"MessagePack: type={mp_msg_type} proto={mp_proto_ver} msgSize={single_msg_size} count={msg_pack_size}")

assert mp_msg_type == 0x0F, "Should be PACK type"
assert single_msg_size == 25

fields = decode_message_pack(msg_pack_data[3:], msg_pack_size, single_msg_size)
print(f"\nDecoded fields:")
for name, val in fields.items():
    print(f"  {name}: {val}")

# Verify key fields
assert "Basic ID" in fields
assert "GB_TEST_00001" in str(fields["Basic ID"])
assert "Speed Horizontal" in fields
assert "8.00 m/s" in str(fields["Speed Horizontal"])
assert "Latitude" in fields
assert abs(fields["Latitude"] - 23.1292) < 0.0001
assert "Longitude" in fields
assert abs(fields["Longitude"] - 113.2645) < 0.0001
print("\n✅ All assertions passed!")
