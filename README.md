# linkerbot-hand

简体中文 | [English](README.en.md)

`linkerbot-hand` 是 Linkerbot 灵巧手的独立 Python SDK，统一提供设备生命周期、
能力描述、非阻塞命令调度、缓存快照、健康状态、安全恢复和可注入测试后端，并内置
L6、O6、L20Lite、L25、L30、O20 和 O30i 驱动。

本包不依赖 ROS、机械臂、运动学、Pydantic、SciPy 或旧聚合 SDK。CAN/CAN FD
通信由轻量的 `linkerbot-transport` 提供。先为 pip 配置公司 Gitea Python 包源及
访问凭据，再安装正式版本：

```bash
python -m pip install linkerbot-hand==0.1.3
```

包源地址为
`https://gitea.linkerhub.work/api/packages/zhuzhixiang/pypi/simple`。请通过环境变量、
pip 的认证配置或系统凭据管理器提供用户名和访问令牌，不要把令牌写入代码或提交到
仓库。

### NumPy 兼容范围

SDK 的触觉矩阵使用 NumPy，但不依赖 SciPy。依赖范围按 Python 版本声明：

- Python 3.10-3.12：`numpy>=1.21.6,<2`。
- Python 3.13 及以上：`numpy>=2.1,<3`。

ROS 2 Jazzy 使用 Python 3.12，安装时会选择 NumPy 1.26.x，与 Ubuntu 系统提供的
SciPy 1.11.4（要求 `numpy<1.28`）兼容。不要在 Jazzy 的
`--system-site-packages` 环境中继续使用 `linkerbot-hand==0.1.1`，该版本错误地要求
`numpy>=2.2.6`。

规范的统一接口入口是 `linkerbot_hand`。同一个 wheel 也提供各型号的
`linkerbot.hand` 驱动，但本版本统一了数值语义：默认控制与反馈都使用协议值，
不再兼容用户 LUT 或旧 raw-angle 别名。

## 快速开始

构造 `HandDevice` 不会连接设备、启动轮询或产生运动。只有显式调用 `open()` 后
才创建设备后端，只有调用 `start_polling()` 后才产生周期性状态查询。

```python
from linkerbot.hand import create_hand_device
from linkerbot_hand import CommandPolicy, PollingPlan

hand = create_hand_device("L6", side="right", interface_name="can0")
hand.open()
try:
    hand.start_polling(PollingPlan(angles_hz=60, force_hz=30, temperature_hz=1))
    handle = hand.submit_percentage_angles(
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
- 协议值命令：`submit_angles()`、`submit_speeds()`、`submit_torques()`。
- 显式百分比命令：`submit_percentage_angles()`；仅在能力明确支持时使用
  `submit_percentage_speeds()`。
- 状态：`get_snapshot()`、`get_health()`、`get_capabilities()`。
- 恢复：`recover()` 或通过 `SessionConfig(auto_reconnect=True)` 启用自动重连。

型号差异由 `get_capabilities()` 返回，包括型号、固件、左右手、关节顺序、数值
范围、传感器、推荐轮询频率、transport、ACK 和清错/使能/标定能力。上层应用
不需要维护另一份容易漂移的型号表。

## 角度坐标

`submit_angles()` 和型号 manager 的 `set_angles()` 直接发送能力声明范围内的协议值；
角度快照的 `SensorSample.values` 也直接返回协议解码值。`submit_speeds()`、
`set_speeds()` 及速度反馈遵循相同原则，不自动换算为百分比或 RPM。

统一接口 `submit_percentage_angles()` 使用 capability 声明的逐轴端点：

- `0`：弯曲或闭合。
- `100`：伸直或张开。

型号 manager 的 `set_percentage_angles()` 是显式的本地换算入口。L6、O6、L20Lite、
L25 按 `0..100 -> 0..255` 线性换算；O20、L30 按每轴最小值到最大值换算；O30i
按实测反向字节换算。后三者在部分轴上与统一 capability 方向不同，百分比数组不能跨
入口直接复用；协议值接口没有这个差异。

L6、O6、L20Lite 和 L25 还明确支持 `submit_percentage_speeds()` /
`set_percentage_speeds()`；O20 的协议速度范围虽然也是 `0..100`，但没有声明为
百分比，因此该接口会拒绝 O20。

每个 `SensorSample` 通过 `unit` 和 `encoding` 标注 `values` 的实际单位与线上标量编码，
并通过 `physical_unit`、`scale`、`offset` 标注已确认的物理解释。仅当 `scale` 和
`offset` 都不是 `None` 时，才可使用 `physical = raw * scale + offset`；只有
`physical_unit` 表示物理维度已知但比例尚未确认。`value_kind` 与 `matrix_shape` 用于区分
普通数值、位掩码、枚举和触觉矩阵。需要 RPM、mA 或物理角度时，应在数据边界显式换算；
SDK 不在命令和遥测主路径隐式换算。

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
`SensorSample` 都包含协议解码值、原始单位、编码、物理元数据、值类型、矩阵尺寸、
采集时间、序号、有效性、过期状态和连接代次。`get_health()` 返回连接状态、成功收发
时间、连续错误、最近错误、重连次数、连接代次、命令覆盖数、待发送数和各类状态年龄。

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

## 型号专用接口

按型号调用的应用可以继续从相同路径导入：

```python
from linkerbot.hand import L6, L30
```

这些 manager 的 `set_angles()`、`set_speeds()`、`set_torques()` 和对应反馈也都使用
协议值；百分比方法带有明确的 `set_percentage_*` 名称。旧 LUT 配置、
`set_raw_angles()` 和 `set_hardware_raw_angles()` 已删除，不提供兼容层。

`linkerbot.hand` 也重导出 `HandDevice`、`HandSession`、`PollingPlan` 和
`CommandPolicy`。独立 wheel 不拥有共享的 `linkerbot` 包根，原来的
`from linkerbot import L6` 应改为 `from linkerbot.hand import L6`。

## 文档

- [统一设备 API Reference](docs/zh/src/reference/hand-device.md)
- [设备能力矩阵](docs/zh/src/reference/hand-capabilities.md)
- [协议值与显式换算](docs/zh/src/reference/angle-coordinates.md)
- [接口兼容边界](docs/zh/src/reference/hand-compatibility.md)
- [迁移指南](docs/zh/src/reference/hand-migration.md)
- [CAN 接入](docs/zh/src/reference/can.md)
- [CAN FD 接入](docs/zh/src/reference/canfd.md)

## 支持范围

- Python 3.10 及以上版本。
- Linux SocketCAN / CAN FD，以及各型号能力描述支持的厂商后端。
- L6、O6、L20Lite、L25、L30、O20 和 O30i。

具体型号功能以运行时 `get_capabilities()` 和
[设备能力矩阵](docs/zh/src/reference/hand-capabilities.md) 为准。
