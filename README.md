# linkerbot-hand

简体中文 | [English](README.en.md)

`linkerbot-hand` 是 Linkerbot 灵巧手的独立 Python SDK，统一提供设备生命周期、
能力描述、非阻塞命令调度、缓存快照、健康状态、安全恢复和可注入测试后端，并内置
L6、O6、L20Lite、L25、L30、O20 和 O30i 驱动。

本包不依赖 ROS、机械臂、运动学、Pydantic、SciPy 或旧聚合 SDK。CAN/CAN FD
通信由轻量的 `linkerbot-transport` 提供。先为 pip 配置公司 Gitea Python 包源及
访问凭据，再安装正式版本：

```bash
python -m pip install linkerbot-hand==0.1.0
```

包源地址为
`https://gitea.linkerhub.work/api/packages/zhuzhixiang/pypi/simple`。请通过环境变量、
pip 的认证配置或系统凭据管理器提供用户名和访问令牌，不要把令牌写入代码或提交到
仓库。

规范的新接口入口是 `linkerbot_hand`。同一个 wheel 继续提供历史
`linkerbot.hand` 包、原有型号类和函数签名，现有用户可以逐步迁移。

## 快速开始

构造 `HandDevice` 不会连接设备、启动轮询或产生运动。只有显式调用 `open()` 后
才创建设备后端，只有调用 `start_polling()` 后才产生周期性状态查询。

```python
from linkerbot.hand import create_hand_device
from linkerbot_hand import CommandPolicy, PollingPlan

hand = create_hand_device("L6", side="right", interface_name="can0")
hand.open()
try:
    hand.start_polling(
        PollingPlan(angles_hz=60, force_hz=30, temperature_hz=1)
    )
    handle = hand.submit_angles(
        [100, 100, 100, 100, 100, 100],
        policy=CommandPolicy.LATEST,
    )
    snapshot = hand.get_snapshot()
    health = hand.get_health()
finally:
    hand.stop_polling()
    hand.close()
```

`open()`、`close()`、使用相同配置的 `start_polling()` 以及 `stop_polling()` 都是
幂等操作。关闭设备时最多等待 `SessionConfig.join_timeout_s`，不会依赖
`__del__` 回收线程或连接。

## 统一接口

所有型号通过相同的 `HandDevice` / `HandSession` 接口使用：

- 生命周期：`open()`、`close()`、`start_polling()`、`stop_polling()`。
- 命令：`submit_angles()`、`submit_raw_angles()`、`submit_speeds()`、
  `submit_torques()` 和受能力约束的离散操作。
- 状态：`get_snapshot()`、`get_health()`、`get_capabilities()`。
- 恢复：`recover()` 或通过 `SessionConfig(auto_reconnect=True)` 启用自动重连。

型号差异由 `get_capabilities()` 返回，包括型号、固件、左右手、关节顺序、数值
范围、传感器、推荐轮询频率、transport、ACK 和清错/使能/标定能力。上层应用
不需要维护另一份容易漂移的型号表。

## 角度坐标

统一百分比接口始终使用以下定义：

- `0`：弯曲或闭合。
- `100`：伸直或张开。

新接口 `submit_raw_angles()` 和快照中的 `raw_values` 始终表示设备协议原始值
（`protocol_raw`），不经过用户 LUT。旧型号接口的 `set_raw_angles()` 为兼容
历史应用，部分型号仍表示经过用户 LUT 的标准 raw；需要精确发送设备原值时使用
`set_hardware_raw_angles()`。

只有能力描述明确提供标定物理范围时，SDK 才支持物理角度转换；否则会抛出
`UnsupportedOperationError`，不会把未知原始值伪装成弧度。完整定义见
[角度坐标 Reference](docs/zh/src/reference/angle-coordinates.md)。

## 非阻塞命令

`submit_*()` 只负责校验并将命令交给调度器，不在调用线程等待设备 I/O。连续运动
命令共用容量为 1 的 latest-wins 槽位，新目标会覆盖尚未发送的旧目标；清错、使能
和标定等离散操作使用独立的有界 FIFO。

`CommandHandle` 至少区分以下阶段：

- `ACCEPTED`：参数有效并进入调度器。
- `DISPATCHED`：transport 已实际发送成功。
- `ACKNOWLEDGED`：设备协议明确确认。
- `FAILED`：发送或通信失败。
- `SUPERSEDED`：发送前被更新目标覆盖。

不支持设备 ACK 的协议以 `DISPATCHED` 作为终态，不会把入队成功描述为
`ACKNOWLEDGED`。可通过 `handle.ack_support` 判断当前命令的 ACK 能力。

## 快照与健康状态

`get_snapshot()` 只复制 SDK 缓存，不发送 CAN 请求或等待设备。每个
`SensorSample` 都包含采集时间、序号、有效性、过期状态、连接代次和可用的协议
原始值。`get_health()` 返回连接状态、成功收发时间、连续错误、最近错误、重连
次数、连接代次、命令覆盖数、待发送数和各类状态年龄。

SDK 统一调度角度、力、温度、电流、速度和故障轮询；上层只提交
`PollingPlan`，不应再建立第二套周期查询任务。

## 安全恢复与测试

`recover()` 会替换并重新验证后端，清除恢复前尚未发送的命令，并在发布新连接
代次前读取当前状态。恢复过程不会重放旧目标、自动张开/握拳或自动清除故障。

`linkerbot_hand.testing` 提供 `FakeBackend`、`FakeBackendFactory`、
`FakeBackendState` 和 `ManualClock`，可以在无实物环境下确定性测试断线、超时、
轮询、命令覆盖和恢复：

```python
from linkerbot_hand import HandDevice, HandSide, get_model_capabilities
from linkerbot_hand.testing import FakeBackendFactory

capabilities = get_model_capabilities("L6", side=HandSide.RIGHT)
hand = HandDevice(capabilities, FakeBackendFactory(capabilities))
```

自定义 `HandBackend.dispatch()` 必须在第一次 transport 发送前调用
`claim_dispatch()`；只有实际发送成功后才能调用一次 `mark_dispatched()`。

## 旧接口兼容

现有按型号调用的应用可以继续使用：

```python
from linkerbot.hand import L6, L30
```

迁移期间，`linkerbot.hand` 也重导出 `HandDevice`、`HandSession`、
`PollingPlan` 和 `CommandPolicy`。独立 wheel 不拥有共享的 `linkerbot` 包根，原来
的 `from linkerbot import L6` 应改为 `from linkerbot.hand import L6`。

## 文档

- [统一设备 API Reference](docs/zh/src/reference/hand-device.md)
- [设备能力矩阵](docs/zh/src/reference/hand-capabilities.md)
- [角度坐标与原始值](docs/zh/src/reference/angle-coordinates.md)
- [旧接口兼容说明](docs/zh/src/reference/hand-compatibility.md)
- [迁移指南](docs/zh/src/reference/hand-migration.md)
- [CAN 接入](docs/zh/src/reference/can.md)
- [CAN FD 接入](docs/zh/src/reference/canfd.md)

## 支持范围

- Python 3.10 及以上版本。
- Linux SocketCAN / CAN FD，以及各型号能力描述支持的厂商后端。
- L6、O6、L20Lite、L25、L30、O20 和 O30i。

具体型号功能以运行时 `get_capabilities()` 和
[设备能力矩阵](docs/zh/src/reference/hand-capabilities.md) 为准。
