
# Drone Remote ID Spoofer 开发者文档

## 1. 架构设计

项目采用分层模块化设计，主要组件如下：

```
drone_rid_spoofer/
├── cli.py                 # 命令行入口，参数解析，配置加载
├── spoofer.py             # 核心控制类 DroneSpoofer（协调者）
├── state.py               # 无人机状态 DroneState
├── drone_factory.py       # 从配置或随机参数创建 DroneState 列表
├── scheduler.py           # 自动模式定时调度（并发发送）
├── manual_controller.py   # 手动模式键盘控制
├── logger_utils.py        # 日志辅助函数
├── helpers.py             # 通用工具（MAC生成、坐标转换等）
├── messages.py            # ASTM 消息编码/解码
├── gb46750_messages.py    # GB46750 数据包编码
├── config_validator.py    # 配置文件校验（P2）
└── transport/             # 传输后端
    ├── base.py            # 抽象基类 TransportBackend
    ├── wifi.py            # Wi-Fi Beacon（支持 ASTM/GB42590/GB46750）
    ├── ble.py             # BLE（仅 ASTM）
    └── nan.py             # Wi-Fi NAN（仅 ASTM）
```

### 1.1 数据流
1. **cli.py** 解析参数/配置，创建 `TransportBackend` 列表。
2. **DroneSpoofer** 初始化，构建 `physical_backends` 映射。
3. 自动模式：
   - **DroneFactory** 生成 `DroneState` 列表。
   - **Scheduler** 周期性地：
     - 更新无人机位置（调用 `update_location` 和 `drift_kinematics`）。
     - 并发提交 `_send(drone)` 任务。
4. `_send`：
   - 根据 `drone.protocol` 选择消息构建函数（ASTM / GB42590 / GB46750）。
   - 根据 `drone.physical_transport` 选择后端，调用 `send_messages(drone, messages, protocol)`。
5. 后端将消息封装为相应的帧（Beacon/BLE/NAN）并注入无线网络。

### 1.2 关键设计模式
- **策略模式**：不同协议编码（ASTM/GB42590/GB46750）通过 `protocol` 字段动态选择。
- **工厂模式**：`DroneFactory` 负责创建 `DroneState`。
- **观察者模式**（调度器回调）：`Scheduler` 在每一周期调用 `send_callback`。
- **模板方法**：`TransportBackend` 定义抽象接口，子类实现具体发送逻辑。

## 2. 添加新协议

### 步骤
1. **定义消息结构**：在 `messages.py` 或新建 `new_protocol_messages.py` 中实现编码函数。
   - 函数签名：`def build_new_protocol_packet(drone: DroneState) -> bytes`。
   - 如有多个消息，可返回 `List[bytes]`。
2. **扩展 `_send` 逻辑**：在 `spoofer.py` 的 `_send` 中添加新的 `elif protocol == "new_protocol"` 分支，调用上述编码函数。
3. **更新配置校验**：在 `config_validator.py` 的 `ALLOWED_PROTOCOLS` 中加入新协议名，并定义必填字段检查函数。
4. **Wi-Fi 后端支持**：如果协议通过 Wi-Fi 传输，在 `wifi.py` 的 `send_messages` 中添加 `elif protocol == "new_protocol"`，实现对应 Vendor IE 封装。
5. **更新文档**：在用户手册中添加协议说明和必填字段。

### 示例（伪代码）
```python
# new_protocol_messages.py
def build_new_protocol_messages(drone: DroneState) -> List[bytes]:
    return [b'\x00\x01\x02' + drone.serial]  # 示例

# spoofer.py
elif protocol == "new_protocol":
    messages = build_new_protocol_messages(drone)
```

## 3. 添加新传输后端

### 步骤
1. **继承 `TransportBackend`**，实现 `send_messages(drone, messages, protocol)` 和 `close()`。
2. **在 `__init__` 中**初始化物理资源（如 socket、网络接口）。
3. **在 `send_messages` 中**：
   - 检查 `protocol` 是否支持，若不支持则记录警告并返回。
   - 将 `messages` 封装为底层帧格式，通过对应接口发送。
4. **在 `cli.py` 的 `create_backends` 中**添加对新传输的解析（如 `-t newtrans`）。
5. **在 `spoofer.py` 的 `__init__` 的 `physical_backends` 映射中添加**新类型的识别。
6. **更新文档**。

### 示例（添加 UDP 传输）
```python
# transport/udp.py
import socket
class UdpBackend(TransportBackend):
    def __init__(self, target_ip, target_port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addr = (target_ip, target_port)
    def send_messages(self, drone, messages, protocol="astm"):
        for msg in messages:
            self.sock.sendto(msg, self.addr)
    def close(self):
        self.sock.close()
```

## 4. 单元测试指南

### 4.1 测试框架
- 使用 `pytest`，测试文件位于 `tests/` 目录。
- 每个测试文件对应一个源模块。

### 4.2 编写测试
#### 通用模式
```python
# tests/test_xxxx.py
import pytest
from drone_rid_spoofer.xxxx import function_to_test

# 推荐内联 fixture（避免 conftest 加载问题）
@pytest.fixture
def sample_drone():
    from drone_rid_spoofer.state import DroneState
    return DroneState(...)   # 使用默认值

def test_function(sample_drone):
    result = function_to_test(sample_drone)
    assert result == expected
```

#### 覆盖内容
- 编码函数应验证输出长度、字段值。
- 解码函数应与编码函数对称测试。
- 状态更新应验证位置变化在预期范围内。
- 配置校验应测试有效与无效配置。

### 4.3 运行测试
```bash
uv run pytest tests/ -v
# 如使用 conftest 失败，可添加 --import-mode=importlib
```

### 4.4 添加新测试
- 为新增功能编写相应测试。
- 确保所有测试通过后再提交代码。

## 5. 代码风格与规范
- 遵循 PEP 8。
- 使用类型注解（`typing`）。
- 公共函数和类需有 docstring。
- 日志使用 `logging` 模块，适当级别（INFO/DEBUG/WARNING/ERROR）。

## 6. 版本发布流程（建议）
1. 更新 `pyproject.toml` 中的 `version`。
2. 更新 `CHANGELOG.md`。
3. 运行完整测试。
4. 构建分发包：`uv build`。
5. 上传至 PyPI（可选）。

---

# 结束

