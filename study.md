## 项目总结：Drone Remote ID Spoofer

### 项目概述

这是一个由瑞士 **Cyber-Defence Campus** 和 **ZHAW** 开发的安全研究工具，用于**伪造无人机远程 ID（Remote ID）数据包**，符合 ASTM F3411-19/22 标准。它通过 Wi-Fi 信标帧和 BLE 广播发送伪造的无人机身份信息，使伪造的无人机出现在兼容的接收器上（如 OpenDroneID App、DroneTag Rider、DJI AeroScope 等）。

---

### 模块分析

#### 1. 入口层

| 文件 | 功能 |
|------|------|
| `spoof_drones.py` | 向后兼容入口，7 行代码直接委托给 `cli.main()` |
| `drone_rid_spoofer/__main__.py` | 支持 `python -m drone_rid_spoofer` 方式启动 |
| `drone_rid_spoofer/cli.py` | **核心入口**：CLI 参数解析、配置文件加载、传输后端组装、主循环启动 |

`cli.py` 支持丰富的命令行参数（接口、手动模式、随机数量、坐标、传输方式等），并通过 `-c` 支持 JSON 场景文件，CLI 参数优先级高于配置文件。

---

#### 2. 数据模型层

| 文件 | 功能 |
|------|------|
| `drone_rid_spoofer/state.py` | `DroneState` 数据类，包含序列号、位置（lat/lng * 10^7）、MAC 地址、运动学参数（速度/高度/方向）、航点、寿命等 22 个字段。支持三种飞行模式：`random`（随机游走）、`static`（静止）、`waypoints`（航点路径）。提供位置更新、运动学漂移、WASD 手动控制方法 |
| `drone_rid_spoofer/helpers.py` | 辅助工具集：坐标解析验证、Wi-Fi/BLE MAC 地址生成（符合规范）、随机序列号/位置/速度/高度生成、漂移函数 |

---

#### 3. 消息构建层

| 文件 | 功能 |
|------|------|
| `drone_rid_spoofer/messages.py` | 实现 ASTM F3411-19/22 标准的 25 字节消息负载构建，包含 5 种消息类型：**Basic ID（类型 0）**、**Location/Vector（类型 1）**、**Self-ID（类型 3）**、**System（类型 4）**、**Operator ID（类型 5）**。对方向角、速度、垂直速度、高度进行 ASTM 标准编码 |

---

#### 4. 核心控制层

| 文件 | 功能 |
|------|------|
| `drone_rid_spoofer/spoofer.py` | **DroneSpoofer 控制器**（297 行），是系统的核心调度器：<br>• **手动模式**：通过 termios cbreak 模式实现 WASD 键盘实时控制单架无人机<br>• **自动模式**：从配置创建多架无人机，循环更新位置（随机游走/航点切换），检查寿命过期，通过所有传输后端发送消息<br>• 支持三种飞行模式的调度逻辑 |

---

#### 5. 传输层

| 文件 | 功能 |
|------|------|
| `transport/base.py` | `TransportBackend` 抽象基类，定义 `send_messages()` 和 `close()` 接口 |
| `transport/wifi.py` | **Wi-Fi 后端**（123 行）：使用 Scapy 构建 802.11 信标帧，将所有 ASTM 消息打包到厂商特定 IE（OUI `0xFA0BBC`），通过**独立后台发送线程**持续广播。支持信道锁定、ESS 能力位设置、序列号和 TSF 自动递增 |
| `transport/ble.py` | **BLE 后端**（303 行）：使用**原始 HCI 套接字**直接控制蓝牙控制器，发送 `ADV_NONCONN_IND` 广播。每条广播只携带一条 ASTM 消息，通过轮转机制实现所有消息类型的发送（Location 消息以 3 倍频率发送以符合合规要求）。多无人机采用**时分复用**方式（切换随机地址 → 设置广播数据 → 短时广播 → 禁用 → 切换下一架） |

---

### 架构数据流

```
场景 JSON / CLI 参数
       │
       ▼
  DroneSpoofer 主循环
       │
       ├── build_all_messages() → 5 条 25 字节 ASTM 负载
       │
       ├── WifiBackend.send_messages()  → 802.11 信标帧（Scapy sendp，独立发送线程持续广播）
       └── BleBackend.send_messages()   → BLE ADV_NONCONN_IND（原始 HCI socket，时分复用轮转）
```

### 关键设计特点

1. **传输无关的消息层**：相同的 ASTM 消息负载可同时用于 Wi-Fi 和 BLE
2. **Wi-Fi 独立发送线程**：持续广播所有活跃无人机的信标帧，不受主循环阻塞
3. **BLE 时分复用**：单无线电切换地址和广播数据，轮流发送多架无人机
4. **MAC 地址规范**：Wi-Fi 使用本地管理单播地址（`0bxxxxxx10`），BLE 使用静态随机地址（`0b11xxxxxx`）
5. **丰富的场景配置**：10 个预设场景覆盖机场入侵、蜂群、压力测试、定时出现、双传输等典型研究场景
6. **需 root 权限**运行（Wi-Fi 监听模式 + BLE HCI 原始套接字）