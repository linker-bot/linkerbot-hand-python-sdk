# linkerbot-hand

`linkerbot-hand` 是 Linkerbot 全系列灵巧手的独立 Python SDK。L6、O6、
L20Lite、L25、O20、L30 和 O30i 共用唯一的 `HandDevice` 接口；型号差异由
`get_capabilities()` 返回，不再公开型号类、manager、`HandSession` 或
`linkerbot.hand` 兼容入口。

英文说明见 [README.en.md](./README.en.md)，完整中文 reference 见
[docs/zh/src/reference](./docs/zh/src/reference/README.md)。

## 设计边界

- 构造 `HandDevice` 和 transport 时零设备 I/O、零线程、零运动。
- `open()`、`close()`、`start_polling()` 和 `stop_polling()` 显式且幂等。
- `set_*()` 非阻塞，返回 `CommandHandle`；连续目标使用 latest-wins。
- 普通 `set_*()` / `get_*()` 使用设备协议原值，不做隐式物理量换算。
- `set_percent_angles()` 使用 `0..100`；屈伸轴统一为
  `0=弯曲/闭合，100=伸直/张开`。
- 查询型 `get_*()` 主动执行一次新事务；声明为 `COMMAND_CACHE` 或
  `COMMAND_RESPONSE` 的 getter 只返回明确标注来源的主机缓存或设备写响应。
  `get_snapshot()` 和 `get_health()` 始终只读内存；专用消费线程可用
  `wait_for_snapshot()` 等待缓存 revision，不产生设备 I/O。
- SDK 是设备周期查询的唯一所有者，ROS 或应用只提交 `PollingPlan`。
- 核心包不依赖 NumPy、SciPy、ROS、机械臂或旧聚合 SDK。

## 安装

从 Linkerbot Gitea PyPI 安装：

```bash
python -m pip install \
  --index-url https://gitea.linkerhub.work/api/packages/zhuzhixiang/pypi/simple \
  --extra-index-url https://pypi.org/simple \
  linkerbot-hand
```

从源码开发安装：

```bash
python -m pip install -e .
```

运行依赖只有 `linkerbot-transport` 及其 CAN/CAN FD backend。Python 要求
`>=3.10`。

## SocketCAN 准备

经典 CAN 型号 L6、O6、L20Lite、L25 使用 1 Mbit/s：

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up
```

L30 和 O20 使用 1 Mbit/s 仲裁速率、5 Mbit/s 数据速率：

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000 dbitrate 5000000 fd on
sudo ip link set can0 up
```

O30i 使用 1 Mbit/s 仲裁速率和数据速率：

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000 dbitrate 1000000 fd on
sudo ip link set can0 up
```

详细配置见 [经典 CAN](./docs/zh/src/reference/can.md) 和
[CAN FD](./docs/zh/src/reference/canfd.md)。

## 唯一调用方式

```python
from linkerbot_hand import AngleFeedbackPolicy, HandDevice, PollingPlan
from linkerbot_transport.can import CANTransport

transport = CANTransport("can0")
hand = HandDevice(
    model="o6",
    side="right",
    transport=transport,
)

hand.open()
try:
    capabilities = hand.get_capabilities()
    print(capabilities.identity)
    print(capabilities.joint_names)

    hand.start_polling(
        PollingPlan(
            angles_hz=60,
            force_hz=30,
            temperature_hz=1,
            faults_hz=5,
            angle_feedback=AngleFeedbackPolicy.ADAPTIVE,
        )
    )

    handle = hand.set_percent_angles([100, 50, 100, 100, 100, 100])
    print(handle.command_id, handle.stage, handle.ack_support)

    fresh = hand.get_angles(timeout=0.2)
    print(fresh.raw)
    print(fresh.percent)

    snapshot = hand.get_snapshot()
    health = hand.get_health()
    print(snapshot.angles)
    print(health.connection_state)
finally:
    hand.stop_polling()
    hand.close()
```

默认 `ADAPTIVE` 会在型号 profile 已验证角度设置返回当前状态时使用该样本，并在
`angles_hz` 不足时主动补询；不返回当前角度的型号则退化为纯主动查询。
`QUERY_ONLY` 强制周期主动查询，`COMMAND_FEEDBACK_ONLY` 只适用于 capability 明确声明
`returns_current_state=True` 的型号。三种策略都不改变显式 `get_angles()` 的主动读取语义，
SDK 也不会自动重复最后一个运动目标。详见
[统一 HandDevice API](./docs/zh/src/reference/hand-device.md)。

`set_percent_angles()` 会产生运动。实机首次验证时应使用当前姿态附近的小幅目标，并先确认
型号、左右手、关节顺序和百分比端点。

L30 不区分左右手，构造时不得传 `side`：

```python
from linkerbot_hand import HandDevice
from linkerbot_transport.canfd import SocketCANFDTransport

l30 = HandDevice(
    model="l30",
    transport=SocketCANFDTransport(
        "can0",
        bitrate=1_000_000,
        data_bitrate=5_000_000,
    ),
)
```

## 值语义

```python
# 以下 `hand` 指上文已经 open 的六轴 O6 设备。
raw_handle = hand.set_angles([255, 128, 255, 255, 255, 255])
percent_handle = hand.set_percent_angles([100, 50, 100, 100, 100, 100])

angles = hand.get_angles(timeout=0.2)
print(angles.raw)  # 设备协议原值
print(angles.percent)  # SDK 固定 capability 映射
print(angles.physical_rad)  # 无可靠物理标定的轴为 None
```

速度配置、实测速度、电流上限、实测电流、无量纲 effort limit 和真实扭矩是不同
通道。调用前应检查 `capabilities.command(...)` 和
`capabilities.telemetry_channel(...)`；不支持的统一方法会抛出
`UnsupportedOperationError`。

## 命令阶段

`CommandHandle` 至少区分：

- `ACCEPTED`：已通过校验并进入有界调度器。
- `DISPATCHED`：transport 已成功发送该命令的全部帧。
- `ACKNOWLEDGED`：设备协议返回了可匹配的明确 ACK。
- `FAILED`：编码、发送、协议或连接失败。
- `SUPERSEDED`：发送前被同命令族的新目标覆盖。

不支持设备 ACK 的型号以 `DISPATCHED` 作为成功终态。该阶段不表示机械到位。

## 测试

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest
ruff check src/linkerbot_hand tests/hand
ruff format --check src/linkerbot_hand tests/hand
ty check src/linkerbot_hand tests/hand/api_v1
python -m build
```

实机测试需要先确认型号、固件、供电、终端电阻和总线配置。L6/O6 不设置最低固件连接
门槛；旧固件缺少可选查询时，SDK 只禁用对应通道并抛出
`UnsupportedFirmwareError`，不会断开其他可用功能。详见
[逐型号接口与固件证据](./docs/zh/src/reference/models/README.md)。

## 文档

- [统一 HandDevice API](./docs/zh/src/reference/hand-device.md)
- [型号能力与查询方式](./docs/zh/src/reference/hand-capabilities.md)
- [协议原值、percent 与角度方向](./docs/zh/src/reference/angle-coordinates.md)
- [破坏性迁移说明](./docs/zh/src/reference/hand-migration.md)
- [逐型号接口、单位与协议说明](./docs/zh/src/reference/models/README.md)

本仓库只包含 Hand SDK。机械臂、运动学和 Pinocchio 属于独立 Arm SDK；ROS 2 消息、
Lifecycle、QoS 和 topic 映射属于 `linkerbot-hand-ros`。
