# Architecture

## Overview
The project builds ASTM F3411-19/22 Remote ID payloads and/or GB 46750-2025 packets,
and transmits them over Wi-Fi beacon frames and/or BLE advertisements. It is
structured as a Python package (`drone_rid_spoofer/`) with a backward-compatible
entry point (`spoof_drones.py`).

## Package structure
```
spoof_drones.py                      # shim → drone_rid_spoofer.cli.main()
drone_rid_spoofer/
├── __init__.py
├── __main__.py                      # python -m drone_rid_spoofer
├── cli.py                           # CLI parsing, config loading, backend wiring
├── state.py                         # DroneState dataclass
├── messages.py                      # ASTM message builders (types 0, 1, 4, 5)
├── helpers.py                       # location parsing, random MAC/serial generation
├── spoofer.py                       # DroneSpoofer (manual + automatic control loops)
├── gb46750_messages.py              # GB 46750-2025 encoder/decoder (21 data items)
├── verify_gb46750_messages.py       # 130-item verification test suite
└── transport/
    ├── __init__.py
    ├── base.py                      # TransportBackend ABC
    ├── wifi.py                      # WifiBackend (Scapy Dot11 beacons)
    ├── ble.py                       # BleBackend (raw HCI ADV_NONCONN_IND)
    ├── gb42590.py                   # GB42590Backend (GB 42590 Wi-Fi Beacon)
    ├── gb46750.py                   # GB46750Backend (GB 46750 Wi-Fi Beacon)
    └── nan.py                       # NanBackend (Wi-Fi NAN)
scenarios/                           # ready-to-use scenario configs
interface-monitor.sh                 # puts Wi-Fi interface into monitor mode
```

## Key abstractions

### ASTM Messages (`messages.py`)
Four pure functions return 25-byte ASTM payloads — identical across ASTM/BLE/NAN transports:
- `build_basic_id(serial)` — Message Type 0
- `build_location_vector(lat, lng, direction)` — Message Type 1
- `build_system(pilot_lat, pilot_lng)` — Message Type 4
- `build_operator_id()` — Message Type 5

### GB 46750 Messages (`gb46750_messages.py`)
Full implementation of GB 46750-2025 Section 5.2 data packet format:
- **`build_gb46750_packet()`** — encodes all 21 data items into a single variable-length packet
  - Header: DataType(0xFF) + Version(0x20) + Length
  - Identifier Flags: 3 bytes with bits 7-1 = item present, bit 0 = extension
  - 21 data items (Table 3): UAS ID, Registration Mark, Operation Category, UA Classification,
    Station Location/Type/Altitude, UA Position, Track Angle, Ground Speed, Relative Height,
    Vertical Speed, Geodetic/Pressure Altitude, Operation Status, Coordinate System, Accuracy
    (NACp/GVA/NACv), Timestamp (6-byte LE ms), Timestamp Accuracy
- **`decode_gb46750_packet()`** — decodes a raw packet back to dictionary
- All field encodings match the ESP32 reference implementation byte-for-byte

### Transport backends (`transport/`)

`TransportBackend` defines `send_messages(drone, messages)` and `close()`.

- **WifiBackend** — wraps all messages into a single vendor-specific IE
  (OUI 0xFA0BBC) inside a Dot11 beacon frame, sent via `scapy.sendp()`.
- **GB42590Backend** — GB 42590-2023 Wi-Fi Beacon backend. Uses same OUI 0xFA0BBC,
  VendType 0x0C, with Message Pack containing counter + ASTM messages.
  SSID prefix: `GB-`.
- **GB46750Backend** — GB 46750-2025 Wi-Fi Beacon backend. Uses OUI 0xFA0BBC,
  VendType 0x0E, with a single packet containing all 21 data items.
  SSID prefix: `GB46750-`.
- **NanBackend** — Wi-Fi NAN (ASTM F3411-22 §A.3) backend using nl80211 for
  NAN Sync Beacon and SDF frames.
- **BleBackend** — sends one message per BLE advertisement using raw HCI
  sockets. AD structure: `[len][type=0x16][UUID=0xFFFA][app=0x0D][counter][payload]`
  = 31 bytes. Rotates through message types with Location at 3x frequency.

### Drone Spoofer (`spoofer.py`)
Iterates over all configured backends for each drone:
```python
# For ASTM/BLE/NAN transports
messages = build_all_messages(drone)
# For GB 46750 transport
gb46750_packet = build_gb46750_packet(...)
for backend in self.backends:
    backend.send_messages(drone, messages)
```

### Drone State (`state.py`)
`DroneState` dataclass holds all drone properties. In addition to ASTM fields, it includes
8 GB 46750-specific fields: `registration_mark`, `operation_category`, `ua_classification`,
`station_location_type`, `horizontal_accuracy`, `vertical_accuracy`, `speed_accuracy`,
`timestamp_accuracy`.

## Data flow
1. User supplies CLI args and/or a scenario JSON config.
2. `cli.main()` resolves config, creates transport backends, initializes `DroneSpoofer`.
3. Drones are created from config entries or randomly generated.
4. Each loop iteration:
   - Drone positions update (manual WASD, random walk, or waypoint advancement).
   - For ASTM-based backends: `build_all_messages()` produces 4 ASTM payloads.
   - For GB 46750 backend: `build_gb46750_packet()` encodes all 21 data items into one packet.
   - Each backend sends the payloads in its transport format.
5. Loop repeats at the configured interval until interrupted or all drones expire.
