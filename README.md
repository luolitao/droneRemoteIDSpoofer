# Drone Remote ID Spoofer

一个用于伪造无人机远程识别（Remote ID）数据包的工具，同时兼容 **ASTM F3411-19/22**、中国 **GB 42590-2023** 及 **GB 46750-2025** 标准，支持 Wi-Fi Beacon（ASTM/GB 42590/GB 46750）、Wi-Fi NAN 和 BLE 五种传输方式。

面向**安全研究人员**、**无人机检测系统开发者**以及任何研究 Remote ID 协议健壮性的人员。Remote ID 协议本身不提供认证或加密完整性，使其在不受控环境中天然容易受到消息注入或身份仿冒攻击。

工具生成包含 ASTM F3411 消息负载的原始 802.11 信标帧和 BLE 广播，使伪造的无人机出现在任何兼容接收器上——包括 OpenDroneID App、DroneTag Rider、DJI AeroScope 及自定义监测系统。

---

## 特性

- **多标准支持** — 同时支持 ASTM F3411-19/22、中国 GB 42590-2023 及 GB 46750-2025 标准
- **多传输方式** — Wi-Fi Beacon（ASTM / GB 42590 / GB 46750）、Wi-Fi NAN、BLE 广播，可单独或组合使用
- **多无人机** — 同时伪造多架无人机，每架具有独立的序列号、MAC 地址和飞行行为
- **三种飞行模式** — 随机游走（物理模型驱动）、静态悬停、预定义航点路径
- **场景配置** — 通过 JSON 定义多无人机场景（19 个开箱即用的预设场景）
- **手动控制** — 使用 WASD 键盘实时控制伪造无人机位置
- **GB 42590 / GB 46750 合规发送** — 动态报文（Location）每秒更新，静态报文逐条轮转发送
- **结构化日志** — 按消息块分组输出（Basic ID / Location / Self ID / System / Operator ID / GB 46750 21 项数据）
- **GB 46750-2025 全覆盖** — 完整实现 Section 5.2 数据包格式，21 个数据项编解码，与 ESP32 参考实现字节级一致

---

## 使用场景

- 测试和验证无人机检测/监测系统（参见配套项目 [RemoteIDReceiver](https://github.com/cyber-defence-campus/RemoteIDReceiver)）
- Remote ID 协议安全性研究
- 接收器容量和性能压力测试
- 开发和调试 RID 相关应用

---

## 快速开始

### 环境要求

| 传输方式 | 硬件 | 软件 |
|---------|------|------|
| **Wi-Fi (ASTM / GB)** | 支持 monitor 模式的 802.11 适配器 | Linux，root 权限，`scapy` |
| **Wi-Fi NAN** | 支持 nl80211 的 Wi-Fi 适配器 | Linux，root 权限，`scapy`，`iw` |
| **BLE** | 蓝牙 HCI 适配器 | Linux，root 权限 |

### 安装

```bash
git clone https://github.com/cyber-defence-campus/droneRemoteIDSpoofer.git
cd droneRemoteIDSpoofer
python3 -m venv .venv
source .venv/bin/activate
pip install scapy
```

### 首次运行

**ASTM Wi-Fi Beacon** — 将网卡设置为 monitor 模式：

```bash
sudo chmod +x ./interface-monitor.sh
sudo ./interface-monitor.sh <interface-name>
sudo .venv/bin/python3 spoof_drones.py -i <interface-name>
```

**GB 42590 Wi-Fi Beacon**（中国国标）：

```bash
sudo .venv/bin/python3 spoof_drones.py -i <interface-name> -t gb
```

**GB 46750 Wi-Fi Beacon**（中国国标 2025）：

```bash
sudo .venv/bin/python3 spoof_drones.py -i <interface-name> -t gb46750
```

**BLE** — 确保蓝牙适配器已启用：

```bash
sudo rfkill unblock all
sudo hciconfig hci0 up
sudo .venv/bin/python3 spoof_drones.py -t ble --ble-adapter hci0
```

**Wi-Fi NAN**：

```bash
sudo .venv/bin/python3 spoof_drones.py -i <interface-name> -t nan
```

伪造的无人机将在范围内的任何 RID 接收器上显示。

<img src="./resources/images/spoofed_mobile_app.jpeg" alt="droneScanner app" width="200" style="margin-right:10px;"/><img src="./resources/images/spoofed_remote_id.png" alt="Remote Drone ID Receiver" width="450"/>

*在 DroneTag iOS 应用和 RemoteIDReceiver 中显示的伪造无人机。*

> **注意**：在 Google Pixel 9 上，仅能通过 BLE 欺骗 OpenDroneID 应用，无法欺骗 DroneTag。

---

## 使用示例

### 场景配置文件

```bash
sudo python3 spoof_drones.py -c scenarios/gb_single.json
```

### 无人机蜂群（5 架）

```bash
sudo python3 spoof_drones.py -i wlan1 -r 5
```

### GB 42590 国标蜂群

```bash
sudo python3 spoof_drones.py -c scenarios/gb_swarm.json
```

### GB 46750-2025 国标

```bash
# 单架无人机
sudo python3 spoof_drones.py -c scenarios/gb46750_single.json

# 完整格式测试（6 架覆盖全部 21 项数据）
sudo python3 spoof_drones.py -c scenarios/gb46750_full_test.json

# 蜂群（5 架）
sudo python3 spoof_drones.py -c scenarios/gb46750_swarm.json
```

### 手动键盘控制

```bash
sudo python3 spoof_drones.py -i wlan1 -m
```

使用 **W/A/S/D** 控制无人机向北/西/南/东飞行，**Ctrl+C** 停止。

### 航点飞行路径

```bash
sudo python3 spoof_drones.py -c scenarios/flight_path.json
```

### 压力测试（20 架无人机）

```bash
sudo python3 spoof_drones.py -c scenarios/stress_test.json
```

### 机场入侵模拟

```bash
sudo python3 spoof_drones.py -c scenarios/airport_incursion.json
```

---

## CLI 参考

| 短参数 | 长参数 | 类型 | 默认值 | 说明 |
|-------|--------|------|--------|------|
| `-i` | `--interface` | `str` | 配置或 `wlan1` | Wi-Fi 注入接口 |
| `-m` | `--manual` | flag | — | 手动模式（WASD 键盘控制） |
| `-r` | `--random` | `int` | 配置或 `1` | 随机无人机数量 |
| `-s` | `--serial` | `str` | 随机 | 自定义序列号（最长 20 字符） |
| `-n` | `--interval` | `float` | 配置或 `1.0` | 数据包发送间隔（秒） |
| `-l` | `--location` | `lat lng` | 配置或广州越秀山 | 基准坐标（十进制度数） |
| `-c` | `--config` | `path` | — | 场景配置 JSON 文件路径 |
| `-v` | `--verbose` | flag | — | 启用 DEBUG 级别详细日志 |
| `-t` | `--transport` | `str` | 配置或 `wifi` | 传输后端：`wifi`、`ble`、`nan`、`gb`、`gb46750`、`both`(wifi+ble)，或逗号分隔组合如 `nan,ble` |
| | `--ble-adapter` | `str` | 配置或 `hci0` | BLE HCI 适配器名称 |
| | `--wifi-ess` | flag | 配置或 false | 设置 ESS 能力位（使信标看起来像 AP） |
| | `--wifi-channel` | `int` | 配置或 `6` | Wi-Fi 广播信道 |
| | `--wifi-beacon-interval` | `float` | 配置或 `0.1024` | Wi-Fi 信标发送间隔（秒） |

**配置优先级**：CLI 参数 > 场景 JSON 配置 > 默认值。

---

## 传输后端对比

| 特性 | Wi-Fi (ASTM) | GB 42590 | GB 46750 | Wi-Fi NAN | BLE |
|------|-------------|----------|----------|-----------|-----|
| **标准** | ASTM F3411-19 | GB 42590-2023 | GB 46750-2025 | ASTM F3411-22 §A.3 | ASTM F3411-22 |
| **帧类型** | 802.11 Beacon | 802.11 Beacon | 802.11 Beacon | NAN Sync Beacon + SDF | ADV_NONCONN_IND |
| **OUI** | `0xFA0BBC` | `0xFA0BBC` | `0xFA0BBC` | `0x506F9A` | N/A (UUID `0xFFFA`) |
| **协议版本** | v2 | v1 | v1 | v2 | v2 |
| **SSID** | `RID-<serial>` | `GB-<serial>` | `GB46750-<serial>` | N/A | N/A |
| **消息打包** | 全部消息打包到单个 IE 221 | counter + Message Pack | 21 项单包 + IE 221 | Message Pack | 每条广播 1 条消息 |
| **数据项数量** | 5 类消息 | 5 类消息 | 21 个数据项 | 5 类消息 | 5 类消息 |
| **发送策略** | 动态 + 1 条轮转静态 | 动态 + 1 条轮转静态 | 完整包每间隔发送 | 动态 + 1 条轮转静态 | 3× Location + 1× 轮转静态 |
| **注入方式** | Scapy `sendp()` | Scapy `sendp()` (monitor) | Scapy `sendp()` (monitor) | nl80211 / `iw mgmt send` | 原始 HCI socket |

> **GB 42590 / GB 46750 发送间隔**：动态报文（Location）每秒 1 次；静态报文逐条轮转发送。GB 46750 使用单一数据包包含全部 21 项数据。

---

## 工作原理

```
场景 JSON / CLI 参数
        |
        v
  +-----------+       +------------------+       +---------------------+
  |  Spoofer  | ----> | encode_basic_id  |       | build_gb46750_packet |
  |  Loop     |       | encode_location  |       | (21 数据项单包)       |
  |           |       | encode_self_id   |       +---------------------+
  |           |       | encode_system    |
  |           |       | encode_operator  |
  +-----------+       +------------------+
        |
        v
  +-----+------+------+------+------+
  |            |             |       |
  v            v             v       v
Wi-Fi        GB 42590   GB 46750    BLE          NAN
Backend      Backend    Backend     Backend      Backend
  |            |          |           |            |
  v            v          v           v            v
802.11       802.11     802.11      HCI raw      NAN SDF
Beacon       Beacon     Beacon      ADV_NONCONN  (Public Action)
(scapy)      (scapy)    (scapy)     (socket)     (nl80211)
```

每个发送周期，Spoofer 为每架无人机构建 ASTM 消息负载（Wi-Fi/BLE/NAN 使用）或 GB 46750 完整数据包（21 项数据），交给所有激活的传输后端。各后端将消息负载封装到各自的帧格式中并发送。

详细架构说明见 [ARCHITECTURE.md](ARCHITECTURE.md)。

---

## 场景配置

场景是定义全局设置和无人机列表的 JSON 文件。最简示例：

```json
{
  "global": { "interface": "wlan1" },
  "drones": [ { "mode": "random" } ]
}
```

每架无人机可以配置独立的飞行模式（`random` / `static` / `waypoints`）、序列号、MAC 地址、位置、寿命和传输覆盖。完整参考见 [CONFIG.md](CONFIG.md)，可复制模板见 [scenario.template.json](scenario.template.json)。

### 预设场景一览

| 文件 | 传输 | 数量 | 说明 |
|------|------|------|------|
| `single_random.json` | Wi-Fi | 1 | 单架随机无人机 |
| `swarm_random.json` | Wi-Fi | 5 | 5 架随机蜂群 |
| `flight_path.json` | Wi-Fi | 1 | 航点飞行（7 个航点） |
| `timed_appearance.json` | Wi-Fi | 3 | 定时出现/消失 |
| `airport_incursion.json` | Wi-Fi | 2 | 机场入侵模拟 |
| `stress_test.json` | Wi-Fi | 20 | Wi-Fi 压力测试 |
| `ble_single.json` | BLE | 1 | 单架 BLE 随机 |
| `ble_swarm.json` | BLE | 5 | BLE 蜂群 |
| `ble_stress_test.json` | BLE | 20 | BLE 压力测试 |
| `dual_transport.json` | Wi-Fi + BLE | 1 | 双传输同时发送 |
| `gb_single.json` | GB 42590 | 1 | 单架 GB 42590 |
| `gb_swarm.json` | GB 42590 | 5 | GB 42590 蜂群 |
| `gb_full_test.json` | GB 42590 | 6 | GB 42590 完整格式测试 |
| `gb46750_single.json` | GB 46750 | 1 | 单架 GB 46750 |
| `gb46750_swarm.json` | GB 46750 | 5 | GB 46750 蜂群 |
| `gb46750_full_test.json` | GB 46750 | 6 | GB 46750 完整格式测试（21 项全覆盖） |
| `nan_single.json` | Wi-Fi NAN | 1 | 单架 NAN |
| `nan_swarm.json` | Wi-Fi NAN | 5 | NAN 蜂群 |
| `nan_ble_dual.json` | NAN + BLE | 1 | NAN + BLE 双传输 |

所有场景默认使用广州越秀山坐标 `[23.1403, 113.2725]`。

---

## 验证工具

项目包含多个验证和诊断脚本：

```bash
# 验证 GB 42590 消息编码（161+ 项测试）
python3 verify_gb_messages.py

# 验证 GB 46750-2025 消息编码（130 项测试）
python3 verify_gb46750_messages.py

# 验证 NAN 帧格式
python3 verify_nan.py

# 嗅探 GB 42590 / NAN Remote ID 帧
python3 -m drone_rid_spoofer.sniff_gb -i <interface>

# PCAP 文件分析
python3 resources/analyze_pcap.py <file.pcap>

# 帧注入诊断
python3 resources/check_injection.py -i <interface>
```

---

## 相关项目

- [RemoteIDReceiver](https://github.com/cyber-defence-campus/RemoteIDReceiver) — 配套无人机监测系统，专为与此 Spoofer 配合测试设计
- [OpenDroneID](https://github.com/opendroneid) — 开源 Remote ID 实现及 Android 接收器应用
- [ASTM F3411-22a](https://www.astm.org/f3411-22a.html) — 本工具实现的 Remote ID 标准
- [GB 42590-2023](https://openstd.samr.gov.cn/) — 中国民用无人驾驶航空器系统安全要求
- [GB 46750-2025](https://openstd.samr.gov.cn/) — 中国民用无人驾驶航空器系统远程识别

---

## 致谢

- Fabia Müller，苏黎世应用科技大学（ZHAW）
- Sebastian Brunner，苏黎世应用科技大学（ZHAW）
- Llorenç Romá，Cyber-Defence Campus

## 免责声明

本仓库作为 [Cyber-Defence Campus](https://www.cydcampus.admin.ch) 论文的一部分创建，仅供学术和安全研究使用。

此处提供的软件为概念验证，不适用于业务或恶意用途。作者不对因使用本代码而产生的任何误用、损害或法律后果承担责任。

使用本软件即表示您同意自行承担风险，并遵守所有适用法律和法规。

## 许可证

MIT
