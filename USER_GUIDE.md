# Drone Remote ID Spoofer 用户手册

## 1. 简介
本项目是一个无人机远程识别（RID）信号模拟器，支持 ASTM F3411、GB 42590‑2023 和 GB 46750‑2025 三种协议，可通过 Wi-Fi Beacon、BLE 或 Wi-Fi NAN 发送模拟数据。适用于合规测试、演示、安全研究等场景。

## 2. 快速开始
```bash
# 安装
git clone <repository>
cd droneRemoteIDSpoofer
uv pip install -e .

# 运行一个示例（使用配置文件）
sudo python3 -m drone_rid_spoofer.cli -c scenarios/mixed_protocols.json
```

## 3. 配置说明
配置文件为 JSON 格式，分为 `global` 和 `drones` 两个顶层键。

### 3.1 全局配置 (`global`)
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `interface` | string | 是 | 网络接口名称（如 `wlan1`） |
| `interval` | number | 是 | 发送间隔（秒），建议 ≥1.0 |
| `location` | [number, number] | 是 | 地面站经纬度（[lat, lng]） |
| `protocol` | string | 否 | 默认编码协议：`astm` / `gb42590` / `gb46750` |
| `physical_transport` | string | 否 | 默认物理传输：`wifi` / `ble` / `nan` / `wifi,ble` 等组合 |
| `wifi` | object | 否 | Wi-Fi 特定参数：`channel`（整数）、`beacon_interval`（秒） |
| `ble` | object | 否 | BLE 特定参数：`adapter`（字符串）、`advertising_interval_ms`（毫秒） |
| `nan` | object | 否 | NAN 特定参数：`channel`（整数） |

### 3.2 无人机配置 (`drones` 数组)
每个无人机对象支持以下字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `mode` | string | 是 | 运动模式：`random` / `static` / `waypoints` |
| `serial` | string | 否 | 产品序列号（最多20字符） |
| `protocol` | string | 否 | 覆盖全局协议，同全局格式 |
| `physical_transport` | string | 否 | 覆盖全局传输，同全局格式 |
| `start_location` | [number, number] | 否 | 初始经纬度，未指定则随机 |
| `pilot_location` | [number, number] | 否 | 遥控站位置，未指定则在无人机附近随机 |
| `operator_id` | string | 视协议而定 | 操作员ID（GB42590/GB46750 必填） |
| `operator_altitude` | number | 否 | 操作员海拔（米），默认随机 |
| `speed` | number | 否 | 水平速度（m/s），默认随机 |
| `vertical_speed` | number | 否 | 垂直速度（m/s），默认随机 |
| `geodetic_altitude` | number | 否 | 大地高度（米 MSL），默认随机 |
| `pressure_altitude` | number | 否 | 气压高度（米 MSL），默认与大地高度相同 |
| `height` | number | 否 | 相对起飞点高度（米），默认随机 |
| `mac` | string | 否 | Wi-Fi MAC 地址（格式 `xx:xx:xx:xx:xx:xx`） |
| `lifespan_seconds` | number | 否 | 运行时长（秒），到期自动停止 |
| `waypoints` | [[number,number,number?]] | 仅 waypoints 模式 | 航点列表 `[lat, lng, hold_seconds]` |

#### 专有字段（GB46750 必填）
| 字段 | 类型 | 说明 |
|------|------|------|
| `registration_mark` | string | 实名登记标志（最多8字符） |
| `operation_category` | integer | 运行类别：0=未定义，1=开放类，2=特定类，3=审定类 |
| `ua_classification` | integer | UA分类：0=微型，1=轻型，2=小型，3=中型，4=大型 |
| `station_location_type` | integer | 遥控站位置类型：0=起飞点，1=遥控站 |
| `horizontal_accuracy` | integer | 水平精度（NACp，0-12） |
| `vertical_accuracy` | integer | 垂直精度（GVA，0-6） |
| `speed_accuracy` | integer | 速度精度（NACv，0-4） |
| `timestamp_accuracy` | integer | 时间戳精度（0-8） |

#### 专有字段（GB42590 必填）
- `operator_id`（字符串）

## 4. 运动模式说明

### 4.1 Random（随机）
无人机在锚点（`start_location` 或 `global.location`）周围随机游走，速度和方向持续缓慢漂移，模拟自然飞行。当超出 `max_roam_radius_m`（默认1000米）时，会逐渐拉回中心。

### 4.2 Static（静态）
无人机固定在 `start_location` 位置，不移动。适合测试静态信号。

### 4.3 Waypoints（航点）
无人机按照 `waypoints` 列表依次移动到每个点，并在每个点停留指定的 `hold_seconds`（默认0秒）。到达最后一个点后，会无限停留在该点（实际会暂停 1 小时，但可被中断）。

## 5. 协议字段解释（GB46750 重点）

GB46750 数据包包含以下数据项（依据标准 Table 3）：

| 项号 | 名称 | 必选 | 说明 |
|------|------|------|------|
| 001 | 唯一产品识别码 | M | 20字节 ASCII，由 `serial` 生成 |
| 002 | 实名登记标志 | M | 8字节 ASCII，由 `registration_mark` 提供 |
| 003 | 运行类别 | O | `operation_category` |
| 004 | UA分类 | M | `ua_classification` |
| 005 | 遥控站位置类型 | M | `station_location_type` |
| 006 | 遥控站位置 | M | 由 `pilot_location` / `anchor_lat/lng` 提供 |
| 007 | 遥控站高度 | M | 由 `operator_altitude` 提供 |
| 008 | UA位置 | M | 由当前 `lat/lng` 提供 |
| 009 | 航迹角 | M | 由 `direction` 提供 |
| 010 | 地速 | M | 由 `speed` 提供 |
| 011 | 相对高度 | O | 由 `height` 提供 |
| 012 | 垂直速度 | O | 由 `vertical_speed` 提供 |
| 013 | 大地高度 | M | 由 `geodetic_altitude` 提供 |
| 014 | 气压高度 | O | 由 `pressure_altitude` 提供 |
| 015 | 运行状态 | M | 固定为“空中”（2） |
| 016 | 坐标系类型 | M | 固定为 WGS-84（0） |
| 017 | 水平精度 | M | `horizontal_accuracy` |
| 018 | 垂直精度 | M | `vertical_accuracy` |
| 019 | 速度精度 | M | `speed_accuracy` |
| 020 | 时间戳 | M | 自动生成当前毫秒时间 |
| 021 | 时间戳精度 | M | `timestamp_accuracy` |

> M = 必选，O = 可选（若未提供则省略该字段）。

## 6. 故障排查常见问题

### Q1: Wi-Fi 发送失败，提示 `Operation not permitted`
- 原因：无 root 权限。Wi-Fi 注入需要 `sudo`。
- 解决：使用 `sudo` 运行命令。

### Q2: 接收端未收到任何数据
- 检查接口是否支持 monitor 模式并已锁定到正确信道。
- 确认 `interface` 名称正确（如 `wlan1`）。
- 使用 Wireshark 抓包确认 Beacon 帧是否发出。

### Q3: GB46750 信号无法识别
- 确保配置了所有必填字段（见上文 GB46750 字段）。
- 确认 `protocol: "gb46750"` 和 `physical_transport: "wifi"` 正确设置。
- 抓包检查 Vendor IE 是否以 `0x0D`（APP_CODE）开头，后跟计数器和 `0xFF` 数据包。

### Q4: BLE 发送失败
- 确认蓝牙适配器可用（`hciconfig -a`）。
- 使用 `sudo` 运行，并确保 `ble_adapter` 正确（默认 `hci0`）。
- BLE 仅支持 ASTM 协议，若协议为 `gb42590`/`gb46750` 会忽略。

### Q5: 配置文件校验报错
- 查看错误信息，补全缺失字段或修正类型。
- 参考本手册第 3 节检查字段名称和格式。

### Q6: 无人机不移动（始终停留原点）
- 确认 `mode` 为 `random` 或 `waypoints`，并提供了必要的坐标。
- 检查 `speed` 是否大于 0。

---

## 7. 示例配置

### 单架 ASTM 无人机（Wi-Fi）
```json
{
  "global": {
    "interface": "wlan1",
    "interval": 1.0,
    "location": [23.1403, 113.2725],
    "physical_transport": "wifi",
    "protocol": "astm"
  },
  "drones": [
    {
      "mode": "random",
      "serial": "ASTM_DRONE_01",
      "start_location": [23.1405, 113.2727],
      "operator_id": "OP-12345"
    }
  ]
}
```

### 多协议混合示例
见项目 `scenarios/mixed_protocols.json`。

---

## 8. 命令行参数速查
```bash
python3 -m drone_rid_spoofer.cli [-h] [-i INTERFACE] [-m] [-r N] [-s SERIAL] [-n INTERVAL]
                                  [-l LAT LNG] [-c CONFIG] [-v] [-t TRANSPORT]
                                  [--ble-adapter ADAPTER] [--wifi-ess] [--wifi-channel CH]
                                  [--wifi-beacon-interval SEC]
```
常用选项：
- `-c CONFIG`：加载配置文件（推荐）。
- `-r N`：自动生成 N 个随机无人机。
- `-m`：手动模式（WASD控制）。
- `-t TRANSPORT`：指定物理传输，如 `wifi,ble`。

完整帮助请运行 `python3 -m drone_rid_spoofer.cli -h`。

---

# 结束
