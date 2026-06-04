# Scenario Config

This document describes the JSON scenario file used by `spoof_drones.py`.

Note that CLI flags override config values.

## Top-level structure
```json
{
  "global": { ... },
  "drones": [ ... ]
}
```

## Global fields
- `interface` (string): Wi-Fi network interface for injection. Default: `wlan1`.
- `interval` (number): seconds between transmission batches. Default: `1.0`.
- `location` ([lat, lng]): base coordinates in decimal degrees. Default: Guangzhou.
- `random` (int): number of random drones if `drones` is empty. Default: `1`.
- `transport` (string): `"wifi"`, `"ble"`, `"both"`, `"gb"`, `"gb46750"`, `"nan"`, or comma-separated combination. Default: `"wifi"`.
- `ble` (object): BLE-specific settings (optional).
  - `adapter` (string): HCI adapter name. Default: `"hci0"`.
  - `advertising_interval_ms` (int): time per BLE advertisement in ms. Default: `200`.
- `wifi` (object): Wi-Fi-specific settings (optional).
  - `channel` (int): Wi-Fi channel for injection. Default: `6`.
  - `ess` (bool): Set ESS capability (make beacon look like an AP). Default: `false`.
  - `beacon_interval` (number): Wi-Fi beacon transmission interval in seconds. Default: `0.1024`.

## Drone fields
Each entry in `drones` describes a single drone. Missing fields are generated
randomly (serial, MAC, and locations).

- `mode` (string): `"random"`, `"static"`, or `"waypoints"`. Default: `"random"`.
- `serial` (string): max 20 chars. Optional.
- `mac` (string): Wi-Fi source MAC address. Must be a **unicast, locally-administered** address
  (byte[0] bits: `0bxxxxxx10`). Randomly generated if omitted. Optional.
- `ble_mac` (string): BLE advertiser address. Must be a **Static Random** address per BT Core Spec §1.3.2
  (byte[0] bits: `0b11xxxxxx`). Randomly generated if omitted. Optional.
- `start_location` ([lat, lng]): initial drone location. Optional.
- `pilot_location` ([lat, lng]): pilot position. Optional.
- `lifespan_seconds` (int): stop transmitting after N seconds. Optional.
- `transport` (string): per-drone transport override (`"wifi"`, `"ble"`, `"both"`). Optional.
- `timestamp_offset_minutes` (number): shift the ASTM Location timestamp by this many minutes. Negative values produce timestamps in the past (e.g., `-5` = 5 minutes ago). Wraps within the hour. Default: `0`. Optional.
- `speed` (number): horizontal speed in m/s. Default: random in `[0, 25]`. Optional.
- `vertical_speed` (number): vertical speed in m/s, positive = climbing. Default: random in `[-5, 5]`. Optional.
- `geodetic_altitude` (number): altitude above WGS-84 ellipsoid in m. Default: random in `[50, 400]`. Optional.
- `pressure_altitude` (number): pressure altitude in m. Default: tracks `geodetic_altitude`. Optional.
- `height` (number): height above takeoff/ground in m. Default: random in `[10, 120]`. Optional.
- `waypoints` (list): required when `mode` is `"waypoints"`.
  - Each waypoint is `[lat, lng, hold_seconds?]`.
  - `hold_seconds` defaults to `0` when omitted.

### GB 46750-2025 specific drone fields
When using `"transport": "gb46750"`, the following additional drone fields are supported:

- `registration_mark` (string): UAS registration mark, up to 8 ASCII characters. Example: `"UAS12345"`.
- `operation_category` (int): 0=Open, 1=Specific, 2=Certified. Default: `0`.
- `ua_classification` (int): 0=Undeclared, 1=Aeroplane, 2=Helicopter, 3=Gyroplane, 4=HybridLift, 5=Ornithopter, 6=Glider, 7=Kite, 8=FreeBalloon, 9=CaptiveBalloon, 10=Airship, 11=FreeFall, 12=Rocket, 13=Tethered, 14=PoweredAircraft, 15=Other. Default: `0`.
- `station_location_type` (int): 0=Takeoff, 1=Dynamic, 2=Fixed. Default: `1`.
- `horizontal_accuracy` (int): NACp value (0-15). Default: `0`.
- `vertical_accuracy` (int): GVA value (0-15). Default: `0`.
- `speed_accuracy` (int): NACv value (0-15). Default: `0`.
- `timestamp_accuracy` (int): Timestamp accuracy class (0-15). Default: `0`.

In `random` mode the kinematic values drift each tick within plausible bounds.
In `static` and `waypoints` modes, the seeded values stay constant.

## Modes

### `random`
Drone performs a random walk around its current position each interval.

### `static`
Drone stays at its starting position.

### `waypoints`
Drone jumps to each waypoint in order and holds for `hold_seconds`. After the
last waypoint, it stays at the final location.

## Transport

### `wifi` (default)
Sends ASTM F3411-19 payloads inside Wi-Fi beacon frames with a vendor-specific
IE (OUI 0xFA0BBC). Requires a Wi-Fi adapter in monitor mode. Note that many receivers will not parse those, specially phone applications don't do good with this method.

### `ble`
Sends ASTM F3411-22 payloads as BLE `ADV_NONCONN_IND` advertisements with
Service Data UUID 0xFFFA. Requires a Linux Bluetooth adapter (HCI) and root.

One message per advertisement — the tool rotates through message types, sending
Location at 3x frequency. Multiple drones are time-multiplexed on the single
radio. With 200ms per ad, ~5 drones fit in a 1-second cycle.

### `both`
Sends on Wi-Fi and BLE simultaneously.

### `gb`
Sends GB 42590-2023 payloads inside Wi-Fi beacon frames with vendor-specific
IE (OUI 0xFA0BBC, VendType 0x0C). SSID prefix: `GB-`. Requires Wi-Fi adapter
in monitor mode.

### `gb46750`
Sends GB 46750-2025 packets inside Wi-Fi beacon frames with vendor-specific
IE (OUI 0xFA0BBC, VendType 0x0E). A single packet encodes all 21 data items.
SSID prefix: `GB46750-`. Requires Wi-Fi adapter in monitor mode.

### `nan`
Sends ASTM F3411-22 payloads via Wi-Fi NAN (Neighbor Awareness Networking) using
NAN Sync Beacon and SDF frames. Requires nl80211 support.

## Examples

Minimal one drone spoofing over Wi-Fi:
```json
{
  "global": { "interface": "wlan1" },
  "drones": [ { "mode": "random" } ]
}
```

Minimal one drone spoofing over BLE only:
```json
{
  "global": {
    "transport": "ble",
    "ble": { "adapter": "hci0" }
  },
  "drones": [ { "mode": "random" } ]
}
```

Both transports:
```json
{
  "global": {
    "interface": "wlan1",
    "transport": "both",
    "ble": { "adapter": "hci0" }
  },
  "drones": [ { "mode": "random" } ]
}
```

Waypoints (the global config is ommited):
```json
{
  "drones": [
    {
      "mode": "waypoints",
      "waypoints": [
        [23.1292, 113.2645, 2],
        [23.1294, 113.2646, 2],
        [23.1296, 113.2647, 2]
      ]
    }
  ]
}
```

GB 46750 with custom fields:
```json
{
  "global": {
    "interface": "wlan1",
    "transport": "gb46750"
  },
  "drones": [
    {
      "mode": "random",
      "serial": "SN1234567890ABCDEF",
      "registration_mark": "UAS12345",
      "operation_category": 0,
      "ua_classification": 2,
      "station_location_type": 1,
      "horizontal_accuracy": 10,
      "vertical_accuracy": 8,
      "speed_accuracy": 6,
      "timestamp_accuracy": 12
    }
  ]
}
```

Full template:
- See `scenario.template.json`.
- See `scenarios/` directory for ready-to-use examples.
