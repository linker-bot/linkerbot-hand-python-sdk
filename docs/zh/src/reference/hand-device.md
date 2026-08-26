# `linkerbot_hand` API Reference

`linkerbot_hand.HandDevice` 和 `HandSession` 为所有手型提供相同的生命周期、轮询、命令、
快照、健康状态和恢复接口。两者当前具有相同的公共方法；`HandDevice` 是面向设备调用方的
具体类型，`HandSession` 是实现生命周期和调度逻辑的核心会话类型。

该包不依赖 ROS。接口只使用普通 Python 枚举和数据类，ROS 消息、QoS、Action 与
Lifecycle 状态转换由 ROS 驱动层负责。

本页是按公开名称查询的 API Reference。各型号的实际能力值见
[七型号能力矩阵](hand-capabilities.md)。

## 模块入口

| 导入路径                         | 公开内容                                            |
| -------------------------------- | --------------------------------------------------- |
| `linkerbot_hand`                 | 会话、配置、能力、命令、状态、枚举和稳定异常        |
| `linkerbot_hand.backends.legacy` | 七种内置旧驱动的统一设备工厂 `create_hand_device()` |
| `linkerbot_hand.testing`         | fake backend、可观测状态、虚拟时钟和测试等待工具    |
| `linkerbot.hand`                 | 迁移期兼容重导出；新代码应优先使用 `linkerbot_hand` |

`linkerbot_hand` 的公开名称以模块 `__all__` 为准。`runtime`、`commands` 和 `capabilities`
子包中的下划线名称属于内部实现，不提供兼容承诺。

## 安装与创建

只使用灵巧手时，可以独立安装手部 SDK：

```bash
pip install linkerbot-hand
```

统一接口通过后端工厂注入协议实现。构造只分配内存中的锁、队列和缓存，不创建后端、不连接
设备，也不启动轮询或运动：

```python
from linkerbot_hand import HandDevice, HandSide, SessionConfig, get_model_capabilities
from linkerbot_hand.testing import FakeBackendFactory

capabilities = get_model_capabilities("L6", side=HandSide.RIGHT)
backend_factory = FakeBackendFactory(capabilities)
hand = HandDevice(
    capabilities,
    backend_factory,
    config=SessionConfig(command_rate_hz=100),
)
```

使用包内七型号硬件驱动时，通过统一后端工厂创建设备：

```python
from linkerbot_hand.backends.legacy import create_hand_device

hand = create_hand_device("L6", side="right", interface_name="can0")
```

`create_hand_device()` 本身同样不连接设备。型号对应的连接参数通过关键字参数传给旧驱动，
例如 classic CAN 型号使用 `interface_name`，CAN FD 型号可以使用 `interface_type`、
`channel`、`node_id` 等参数。

### `create_hand_device()`

```text
create_hand_device(
    model: str,
    *,
    side: HandSide | str = HandSide.UNKNOWN,
    session_config: SessionConfig | None = None,
    clock: Clock | None = None,
    **transport_options: Any,
) -> HandDevice
```

| 参数                | 含义                                                                 |
| ------------------- | -------------------------------------------------------------------- |
| `model`             | 型号名；支持 `L6/O6/L20Lite/L25/L30/O20/O30i`，匹配不区分大小写。    |
| `side`              | `left`、`right` 或 `unknown`；也可传 `HandSide`。                    |
| `session_config`    | 命令频率、队列、关闭等待和自动重连配置；`None` 使用默认值。          |
| `clock`             | 可注入时钟；生产环境通常留空，确定性测试可传 `ManualClock`。         |
| `transport_options` | 原样保存并在 `open()` 时传给对应型号旧驱动，不会在构造阶段连接设备。 |

参数无效时抛出 `ValidationError`。构造成功只表示配置已创建，不表示设备在线。
各型号 transport 参数见 [CAN](can.md) 和 [CAN FD](canfd.md) Reference。

### `HandDevice()` / `HandSession()`

```text
HandDevice(
    capabilities: DeviceCapabilities,
    backend_factory: BackendFactory,
    *,
    config: SessionConfig | None = None,
    clock: Clock | None = None,
)
```

高级集成或测试可直接构造会话。`backend_factory` 必须是无参数 callable，并且每次 `open()` 或
恢复时能够返回一个新的 `HandBackend`。构造器只验证参数并创建内存状态，不调用 factory。

### `get_model_capabilities()`

```text
get_model_capabilities(
    model: str,
    *,
    side: HandSide | str = HandSide.UNKNOWN,
    firmware: str | None = None,
    transport: TransportType | None = None,
) -> DeviceCapabilities
```

该函数只查内置注册表，不产生设备 I/O。`firmware` 和 `transport` 覆盖主要用于自定义后端和
测试；硬件会话应优先使用 `create_hand_device()`，并以连接后的 `hand.get_capabilities()` 为准。

## 生命周期

```python
from linkerbot_hand import PollingPlan

hand.open()
try:
    hand.start_polling(PollingPlan(angles_hz=60, force_hz=30, temperature_hz=1))
    # 提交命令、读取缓存
finally:
    hand.stop_polling()
    hand.close()
```

| 方法                    | 返回值       | 行为                                                                                       |
| ----------------------- | ------------ | ------------------------------------------------------------------------------------------ |
| `open()`                | `HandDevice` | 创建后端、连接并验证设备身份，然后启动本会话的调度线程。不会启动传感器轮询或发送运动目标。 |
| `close()`               | `None`       | 停止轮询和调度、使未发送命令失败并释放后端；等待上限由 `join_timeout_s` 控制。             |
| `start_polling(plan)`   | `None`       | 按显式 `PollingPlan` 启动 SDK 唯一的周期查询源；相同计划重复调用无副作用。                 |
| `stop_polling()`        | `None`       | 禁止新查询并有界等待已开始的查询结束。                                                     |
| `recover(timeout=None)` | `None`       | 替换后端、重新验证身份并读取当前状态，成功后增加连接代次。                                 |

`open()`、`close()`、相同计划的 `start_polling()` 和 `stop_polling()` 均可重复调用。
上下文管理器等价于 `open()` / `close()`，但轮询仍必须显式启停。

如果底层 backend 关闭失败，`close()` 会保留该 backend 并抛出稳定错误；在后续
`close()` 成功前，`open()` 会以 `DeviceNotReadyError` 拒绝创建第二条连接。调用方应记录错误并
重试关闭，不能把一次失败的 `close()` 当作资源已经释放。

`open()` 会等待连接和身份验证完成；`recover()` 还会在新连接上读取初始状态。
`recover(timeout)` 的 timeout 必须为有限非负秒数，超时抛出 `TimeoutError`。它们属于生命周期
管理操作，不应在 ROS 命令 callback 中调用。

## `SessionConfig`

```text
SessionConfig(
    command_rate_hz: float = 100.0,
    discrete_queue_size: int = 16,
    join_timeout_s: float = 2.0,
    auto_reconnect: bool = False,
    reconnect_initial_delay_s: float = 0.1,
    reconnect_max_delay_s: float = 2.0,
)
```

| 字段                        | 约束     | 含义                                           |
| --------------------------- | -------- | ---------------------------------------------- |
| `command_rate_hz`           | 有限正数 | 连续运动目标交给 transport 的最高频率。        |
| `discrete_queue_size`       | 正整数   | 清错、使能、禁用和标定 FIFO 的容量。           |
| `join_timeout_s`            | 有限正数 | 停轮询、关闭 worker 和并发生命周期操作的上限。 |
| `auto_reconnect`            | `bool`   | 是否由 SDK worker 自动执行指数退避恢复。       |
| `reconnect_initial_delay_s` | 有限正数 | 第一次自动重连前的延时。                       |
| `reconnect_max_delay_s`     | 有限正数 | 自动重连延时上限，且不得小于初始延时。         |

任一约束不满足时，构造配置即抛出 `ValidationError`。

## 轮询计划

`PollingPlan` 的字段单位均为 Hz，`0` 表示禁用该数据源：

```python
plan = PollingPlan(
    angles_hz=60,
    force_hz=30,
    temperature_hz=1,
    current_hz=10,
    faults_hz=5,
    speeds_hz=0,
    torques_hz=0,
    voltage_hz=0,
    acceleration_hz=0,
    motion_time_hz=0,
)
hand.start_polling(plan)
```

仅可启用 `get_capabilities().sensors` 中声明的数据源，否则抛出
`UnsupportedOperationError`。调用方不得再创建第二个周期设备查询循环；ROS 定时器只能读取
`get_snapshot()`，不能同时调用旧 manager 的 `get_blocking()`。

`PollingPlan` 是不可变数据类。所有字段必须是有限非负数：

| 字段              | `SensorType`   | 默认值 |
| ----------------- | -------------- | -----: |
| `angles_hz`       | `ANGLES`       |    0.0 |
| `force_hz`        | `FORCE`        |    0.0 |
| `temperature_hz`  | `TEMPERATURE`  |    0.0 |
| `current_hz`      | `CURRENT`      |    0.0 |
| `faults_hz`       | `FAULTS`       |    0.0 |
| `speeds_hz`       | `SPEEDS`       |    0.0 |
| `torques_hz`      | `TORQUES`      |    0.0 |
| `voltage_hz`      | `VOLTAGE`      |    0.0 |
| `acceleration_hz` | `ACCELERATION` |    0.0 |
| `motion_time_hz`  | `MOTION_TIME`  |    0.0 |

`rates()` 返回全部频率的 `dict[SensorType, float]`，`enabled_rates()` 只返回大于 0 的项。

## 非阻塞命令

以下方法完成同步参数校验和入队后立即返回 `CommandHandle`，不等待总线发送：

```python
handle = hand.submit_angles(values)
handle = hand.submit_percentage_angles(percentages)
handle = hand.submit_speeds(values)
handle = hand.submit_percentage_speeds(percentages)
handle = hand.submit_torques(values)
```

| 方法                                                               | 输入单位/约束                                          |
| ------------------------------------------------------------------ | ------------------------------------------------------ |
| `submit_angles(values, *, policy=CommandPolicy.LATEST)`            | 每关节一个协议位置值，范围和整数约束来自能力。         |
| `submit_percentage_angles(values, *, policy=CommandPolicy.LATEST)` | 显式 `0..100` 位置，经 capability 映射为协议值。       |
| `submit_speeds(values, *, policy=CommandPolicy.LATEST)`            | 每关节一个协议速度值，单位和值域来自 `speed_range`。   |
| `submit_percentage_speeds(values, *, policy=CommandPolicy.LATEST)` | 显式 `0..100` 速度；仅能力明确声明的型号支持。         |
| `submit_torques(values, *, policy=CommandPolicy.LATEST)`           | 每关节一个协议力矩或电流限制值，范围来自能力。         |
| `submit_command(command, *, policy=None)`                          | 提交完整 `HandCommand`；通常供通用封装使用。           |
| `clear_faults()` / `enable()` / `disable()` / `calibrate()`        | 无参数离散命令，必须先由 `features` 声明支持。         |

所有连续命令的值数量必须等于 `capabilities.joint_count`。值不是有限数字、数量或范围不符时抛出
`ValidationError`；能力未声明时抛出 `UnsupportedOperationError`；会话未打开或不在线时分别抛出
`ClosedError` 或 `DeviceNotReadyError`。

也可以调用 `submit_command(HandCommand(...), policy=...)`。连续运动命令只允许
`CommandPolicy.LATEST`，所有角度、速度和力矩目标共享一个容量为 1 的 latest-wins
槽；新目标会把尚未发送的旧目标置为 `SUPERSEDED`。`clear_faults()`、`enable()`、
`disable()` 和 `calibrate()` 是独立的有界 FIFO，容量由 `SessionConfig.discrete_queue_size`
控制，满时抛出 `QueueFullError`。

### 命令阶段

| 阶段           | 精确定义                                                     | 是否终态                |
| -------------- | ------------------------------------------------------------ | ----------------------- |
| `ACCEPTED`     | 参数和能力校验通过，命令已进入调度器，尚未表示发生设备 I/O。 | 否                      |
| `DISPATCHED`   | 后端确认 transport 实际发送成功。                            | 不支持设备 ACK 时为终态 |
| `ACKNOWLEDGED` | 设备协议明确确认该命令。                                     | 是                      |
| `FAILED`       | 发送、通信或设备确认失败；详情见 `handle.error`。            | 是                      |
| `SUPERSEDED`   | 连续目标在发送前被更新目标覆盖。                             | 是                      |

`handle.ack_support` 明确表示该命令是否支持设备 ACK。没有 ACK 的型号或命令在
`DISPATCHED` 后完成，绝不会把“入队成功”或“发送成功”描述为 `ACKNOWLEDGED`。机械是否到位
不属于 ACK，调用方应根据快照中的状态反馈判断。

`handle.wait(timeout)` 只等待句柄到达终态；不要在 ROS subscription callback 中调用它。

### `HandCommand` 与 `CommandHandle`

```text
HandCommand(kind: CommandKind, values: tuple[float | int, ...] = ())
```

`CommandHandle` 由 submit 方法创建，调用方不应自行实例化。公开读取接口如下：

| 成员                 | 类型                            | 含义                              |
| -------------------- | ------------------------------- | --------------------------------- |
| `command_id`         | `int`                           | 会话内递增的命令编号。            |
| `command`            | `HandCommand`                   | 经过规范化的命令。                |
| `ack_support`        | `AckSupport`                    | 此命令是否有明确设备 ACK。        |
| `stage`              | `CommandStage`                  | 当前阶段。                        |
| `history`            | `tuple[CommandTransition, ...]` | 带 wall-clock 时间的阶段历史。    |
| `error`              | `ErrorInfo \| None`             | 失败分类、类型和消息。            |
| `done`               | `bool`                          | 是否已到达终态。                  |
| `wait(timeout=None)` | `CommandStage`                  | 等待终态；超时抛 `TimeoutError`。 |

`CommandTransition` 包含 `stage: CommandStage` 和 `timestamp: float`；时间戳由会话的
`Clock.time()` 产生。

## 缓存快照

### 查询方法

| 方法                 | 返回值               | 设备 I/O | 可调用状态                     |
| -------------------- | -------------------- | -------- | ------------------------------ |
| `get_capabilities()` | `DeviceCapabilities` | 无       | 构造后任意状态                 |
| `get_snapshot()`     | `HandSnapshot`       | 无       | 构造后任意状态；关闭后样本无效 |
| `get_health()`       | `HealthStatus`       | 无       | 构造后任意状态                 |

`get_snapshot()` 只持有会话锁并复制缓存，绝不调用后端、发送 CAN 请求或等待设备响应：

```python
from linkerbot_hand import SensorType

snapshot = hand.get_snapshot()
angles = snapshot.sample(SensorType.ANGLES)
if angles is not None and angles.valid and not angles.stale:
    print(angles.sequence, angles.collected_at, angles.values)
```

### `HandSnapshot`

| 字段/方法                                 | 类型                       | 含义                           |
| ----------------------------------------- | -------------------------- | ------------------------------ |
| `captured_at`                             | `float`                    | 创建快照时的 wall-clock 秒数。 |
| `snapshot_sequence`                       | `int`                      | 缓存内容变化时递增的序号。     |
| `connection_generation`                   | `int`                      | 当前连接代次。                 |
| `joints`                                  | `tuple[JointState, ...]`   | 按能力关节顺序排列的聚合状态。 |
| `samples`                                 | `tuple[SensorSample, ...]` | 每种能力传感器的独立样本。     |
| `sample(sensor)`                          | `SensorSample \| None`     | 按 `SensorType` 查样本。       |
| `angles/force/temperature/current/faults` | `SensorSample \| None`     | 五种常用样本的便捷属性。       |

### `SensorSample`

| 字段                    | 类型                       | 含义                                                       |
| ----------------------- | -------------------------- | ---------------------------------------------------------- |
| `sensor`                | `SensorType`               | 数据源。                                                   |
| `values`                | `tuple[SensorValue, ...]`  | 解码后的协议值；力传感器元素为 `TactileState`。           |
| `unit`                  | `str \| None`              | `values` 的单位；原始协议计数统一为 `count`。              |
| `encoding`              | `str \| None`              | 标量线上编码，例如 `u8`、`i16_le` 或 `i16_be`。            |
| `physical_unit`         | `str \| None`              | 对应物理量单位；存在不代表比例已确认。                     |
| `scale` / `offset`      | `float \| None`            | 已确认的 `physical = raw * scale + offset` 参数。          |
| `value_kind`            | `SensorValueKind`          | `numeric`、`bitmask`、`enum` 或 `tactile_matrix`。         |
| `matrix_shape`          | `tuple[int, int] \| None`  | 每指触觉矩阵的 `(rows, columns)`。                          |
| `collected_at`          | `float \| None`            | 成功采集时的 wall-clock 秒数。                             |
| `sequence`              | `int`                      | 该数据源成功更新的序号。                                   |
| `valid`                 | `bool`                     | 是否属于当前连接代次且可用。                               |
| `stale`                 | `bool`                     | 年龄是否超过能力的 `stale_after_s`。                       |
| `connection_generation` | `int`                      | 产生该样本的连接代次。                                     |

`JointState` 字段为 `name`、`angle`、`speed`、`torque`、`fault`；前三者直接取自有效的
协议值样本。
`TactileState` 字段为 `finger`、`rows`、`columns`、二维 `values` 和可选 `online`。

每个 `SensorSample` 同时携带协议值、原始单位/编码、物理元数据和值语义。从未成功采集、
断线或属于旧连接代次的数据会标记为无效；是否过期由能力中的 `stale_after_s` 判定。触觉
阵列使用普通数据类 `TactileState`，调用方不需要导入型号专用类型。

`unit` 始终是 `values` 当前使用的单位。仅当 `scale` 与 `offset` 都不是 `None` 时，调用方
才可执行 `physical = raw * scale + offset`。只有 `physical_unit` 表示物理维度已知但协议
比例尚未确认。触觉编码描述单个点，因此是 `u8`；矩阵语义和尺寸分别由 `value_kind` 与
`matrix_shape` 表示。

角度、速度、力矩和电流样本的 `values` 都是协议解码值，不隐式归一化或换算物理单位。
完整定义见[协议值、百分比与显式换算](./angle-coordinates.md)。L6 的速度传感器样本不存在，
因为设备不提供当前速度读回；型号 manager 的速度快照只是最近目标值，不是测量值。

## 健康状态

`get_health()` 同样不产生设备 I/O，返回 `HealthStatus`：

| 字段                             | 类型                       | 含义                             |
| -------------------------------- | -------------------------- | -------------------------------- |
| `connection_state` / `connected` | `ConnectionState` / `bool` | 连接阶段与是否可以正常收发。     |
| `last_successful_send_at`        | `float \| None`            | 最近成功发送的 wall-clock 时间。 |
| `last_successful_receive_at`     | `float \| None`            | 最近成功读取的 wall-clock 时间。 |
| `consecutive_errors`             | `int`                      | 自最近成功通信后的连续错误数。   |
| `recent_error`                   | `ErrorInfo \| None`        | 最近稳定错误分类、类型与消息。   |
| `reconnect_count`                | `int`                      | 已成功完成的恢复次数。           |
| `connection_generation`          | `int`                      | 当前连接代次。                   |
| `superseded_commands`            | `int`                      | latest-wins 覆盖的命令累计数。   |
| `pending_commands`               | `int`                      | 当前等待或正在发送的命令数量。   |
| `data_ages`                      | `tuple[DataAge, ...]`      | 各 `SensorType` 的缓存年龄秒数。 |

`DataAge` 包含 `sensor: SensorType` 和 `age_s: float | None`。从未成功采集时 `age_s` 为
`None`。默认 `SystemClock` 的时间戳是 Unix wall-clock 秒数；注入自定义 `Clock` 后由该时钟
定义。数据年龄始终由同一时钟的单调时间计算，调用方不应自行用 wall-clock 时间相减得到年龄。

## 能力与角度单位

`get_capabilities()` 是型号、固件、左右手、关节顺序、范围、传感器、推荐轮询频率、
transport、可选操作和逐命令 ACK 支持的唯一运行时数据源。连接成功后，固件与设备身份会
补充进能力对象。ROS 不应维护另一份 `MODEL_SPECS`。

### 能力数据类

| 类型/字段              | 公开内容                                                                                                 |
| ---------------------- | -------------------------------------------------------------------------------------------------------- |
| `DeviceCapabilities`   | `model/firmware/side/joints/sensors/transport/features/percentage_convention`                            |
| `joint_names`          | 从 `joints` 推导的协议顺序名称元组。                                                                     |
| `joint_count`          | 关节数量。                                                                                               |
| `sensor(sensor)`       | 返回对应 `SensorCapability`，不支持时为 `None`。                                                         |
| `supports_sensor(...)` | 是否支持指定数据源。                                                                                     |
| `JointCapability`      | `index/name/raw_angle_range/percentage_mapping/speed_range/torque_range/percentage_speed_supported/motion_type/coupled_with` |
| `SensorCapability`     | `sensor/recommended_hz/stale_after_s/unit/encoding/value_range/physical_unit/scale/offset/value_kind/matrix_shape` |
| `DeviceFeatures`       | `device_ack/clear_faults/enable/calibration/acknowledged_commands`                                       |
| `NumericRange`         | `minimum/maximum/unit/integer_only/step/physical_unit/physical_minimum/physical_maximum/scale/offset`    |
| `PercentageMapping`    | `raw_open/raw_closed/raw_at_zero/raw_at_hundred/physical_angle/semantics/zero_endpoint/hundred_endpoint` |
| `DeviceIdentity`       | `model/firmware/side/serial_number`；供 backend 的 `open()` 返回                                         |

`DeviceFeatures.supports_ack(command_kind)` 用于逐命令查询 ACK。不要只读取 `device_ack` 后假定
全部命令均有 ACK。

`JointCapability.raw_angle_range` 为 `None` 时不能提交协议角度；`percentage_mapping` 为
`None` 时不能使用百分比角度转换。相关调用会抛出 `UnsupportedOperationError`。L30 不按左右手区分
角度映射；左手、右手和未知侧均使用同一套协议 v6 原始位置范围与百分比映射，设备身份中的
`side` 不会改变关节能力。

屈伸关节的统一百分比约定始终为 `0=弯曲/闭合，100=伸直/张开`。侧摆、横滚、旋转和
手腕没有“开合”语义，但也提供可逆的 `0..100` 位置归一化；其 0/100 物理端点必须读取
`zero_endpoint` / `hundred_endpoint`，不能解释为弯曲/张开。SDK 提供：

- `percent_to_raw()` / `raw_to_percent()`；
- `submit_percentage_angles()`；
- `percentage_speeds_to_raw()` / `raw_speeds_to_percentage()`；
- `submit_percentage_speeds()`；
- `raw_to_physical_angles()` / `physical_angles_to_raw()`。

### 角度转换方法

| 方法                                | 返回值                        | 约束                                      |
| ----------------------------------- | ----------------------------- | ----------------------------------------- |
| `percent_to_raw(values)`            | `tuple[int, ...]`             | 百分比转协议位置；每轴必须为 0..100。     |
| `raw_to_percent(values)`            | `tuple[float, ...]`           | 协议位置转逐关节归一化位置。              |
| `percentage_speeds_to_raw(values)`  | `tuple[float \| int, ...]`    | 百分比速度转协议值；能力必须明确支持。    |
| `raw_speeds_to_percentage(values)`  | `tuple[float, ...]`           | 协议速度转百分比；能力必须明确支持。      |
| `raw_to_physical_angles(values)`    | `tuple[float, ...]`           | 协议位置转能力声明的物理单位。            |
| `physical_angles_to_raw(values)`    | `tuple[int, ...]`             | 物理角度转协议位置。                      |

这些方法只执行本地计算，不要求 `open()`，也不产生设备 I/O。输入数量必须等于关节数；范围或类型
无效时抛出 `ValidationError`。缺少百分比映射或物理角度能力时抛出
`UnsupportedOperationError`。`raw_to_percent()` 接受任意有限原始值并按端点线性换算，不进行
截断；输入超出映射端点时，返回值也可能超出 `0..100`。

`PercentageMapping.semantics` 为 `OPENNESS` 时表示屈伸开合量，为 `POSITION` 时表示非屈伸
轴在两个实测物理端点之间的位置。`raw_open/raw_closed` 为兼容已有调用方保留；新代码应使用
语义准确的 `raw_at_zero/raw_at_hundred`。若某型号以后存在尚未验证映射的轴，应将
`percentage_mapping` 设为 `None` 并使用原始接口，不能用两个相同 raw 端点伪造映射。只有能力
明确给出 `physical_angle` 时才支持物理角度换算，否则抛出 `UnsupportedOperationError`，协议
位置值不得当作弧度使用。

## 恢复与错误

`SessionConfig(auto_reconnect=True)` 可启用指数退避自动重连，默认关闭。手动 `recover()` 和
自动恢复都会清除旧的待发送运动命令，重新验证型号、固件和左右手，并在发布新连接代次前
读取当前支持状态。恢复不会重放断线前目标，不会自动张开、握拳、使能、标定或清故障。

调用方应按稳定类型或 `ErrorCode` 映射错误，不要解析消息字符串：

`ValidationError`、`UnsupportedOperationError`、`DeviceNotReadyError`、
`TransportError`、`TimeoutError`、`DeviceFaultError`、`QueueFullError`、`ClosedError`。

## 枚举 Reference

| 枚举                  | 成员值                                                                                                               |
| --------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `HandSide`            | `left`, `right`, `unknown`                                                                                           |
| `TransportType`       | `can`, `can_fd`, `fake`, `custom`                                                                                    |
| `SensorType`          | `angles`, `speeds`, `torques`, `force`, `temperature`, `current`, `faults`, `voltage`, `acceleration`, `motion_time` |
| `CommandKind`         | `angles`, `speeds`, `torques`, `clear_faults`, `enable`, `disable`, `calibrate`                                      |
| `CommandPolicy`       | `latest`, `fifo`                                                                                                     |
| `CommandStage`        | `accepted`, `dispatched`, `acknowledged`, `failed`, `superseded`                                                     |
| `AckSupport`          | `supported`, `unsupported`                                                                                           |
| `ConnectionState`     | `closed`, `connecting`, `connected`, `reconnecting`, `error`                                                         |
| `PercentageSemantics` | `openness`, `position`, `grip_pose_interpolation`                                                                    |
| `SensorValueKind`     | `numeric`, `bitmask`, `enum`, `tactile_matrix`                                                                      |
| `JointMotionType`     | `unknown`, `flexion`, `abduction`, `roll`, `rotation`, `wrist`                                                       |
| `JointEndpoint`       | 每轴 0/100 对应的物理姿态端点；包括弯曲、伸直、掌心方向及相邻手指方向等稳定枚举值。                                  |

## 异常 Reference

所有稳定异常均继承 `HandSDKError`，并提供机器可读的 `error.code: ErrorCode`。
`DeviceStateError` 是生命周期状态错误的公共基类，`DeviceNotReadyError` 和 `ClosedError`
继承它：

| 异常                        | `ErrorCode`             | 典型条件                                 |
| --------------------------- | ----------------------- | ---------------------------------------- |
| `DeviceStateError`          | `device_not_ready`      | 捕获全部生命周期状态错误的基类。         |
| `ValidationError`           | `validation`            | 类型、数量、有限性、范围或 policy 错误。 |
| `UnsupportedOperationError` | `unsupported_operation` | 能力未声明传感器、控制或离散操作。       |
| `DeviceNotReadyError`       | `device_not_ready`      | 连接、关闭或恢复状态不允许当前操作。     |
| `TransportError`            | `transport`             | 打开、发送、接收或关闭 transport 失败。  |
| `TimeoutError`              | `timeout`               | 命令等待、恢复或有界关闭超时。           |
| `DeviceFaultError`          | `device_fault`          | 协议明确报告设备故障。                   |
| `QueueFullError`            | `queue_full`            | 离散命令 FIFO 已满。                     |
| `ClosedError`               | `closed`                | 在关闭会话上调用需要连接的操作。         |

异步失败通过 `CommandHandle.error: ErrorInfo | None` 返回；`ErrorInfo` 包含 `code`、`message`
和 `error_type`。非 SDK 异常归一化失败时使用 `ErrorCode.INTERNAL`。同步校验和生命周期错误直接
抛出上述异常。

## 测试注入

`linkerbot_hand.testing` 提供 `FakeBackend`、`FakeBackendFactory`、`FakeBackendState`、
`ManualClock` 和 `wait_until()`。测试可以直接注入读数、发送或轮询异常、断线状态和虚拟时间，
验证超时、丢帧、重连、队列覆盖与故障路径，无需 ROS 或实物。

```python
factory = FakeBackendFactory(capabilities)
hand = HandDevice(capabilities, factory)
state = factory.state
```

| 测试接口                                             | 用途                                              |
| ---------------------------------------------------- | ------------------------------------------------- |
| `state.set_reading(sensor, values)`                  | 设置下一次该传感器轮询返回的协议读数。            |
| `state.disconnect()` / `state.reconnect()`           | 注入 transport 断开或恢复。                       |
| `state.dispatch_errors` / `state.poll_errors`        | 按顺序注入发送或轮询异常。                        |
| `state.dispatched_commands` / `state.poll_log`       | 检查实际发送命令和实际查询的数据源。              |
| `state.io_count`                                     | 发送次数与查询次数之和。                          |
| `open_count/close_count/active_connections`          | 检查生命周期和连接泄漏。                          |
| `ManualClock.advance(seconds)`                       | 确定性推进 wall-clock 和单调时间。                |
| `wait_until(predicate, timeout_s=1.0)`               | 等待真实 worker 条件，超时抛内置 `TimeoutError`。 |

## 自定义 backend Reference

`HandBackend` 是运行在 SDK 单 I/O worker 上的同步协议。自定义实现必须提供：

| 方法            | 返回值           | 契约                                         |
| --------------- | ---------------- | -------------------------------------------- |
| `open()`        | `DeviceIdentity` | 打开 transport 并返回身份，由 session 验证。 |
| `close()`       | `None`           | 幂等、及时中断阻塞 I/O 并释放资源。          |
| `poll(sensor)`  | `BackendReading` | 执行一次阻塞查询；数据源必须与请求一致。     |
| `dispatch(...)` | `DispatchResult` | 声明发送和可选 ACK 结果。                    |

`BackendReading` 包含 `sensor` 和解码后的协议 `values`；`DispatchResult` 的
`acknowledged` 为 `True`、`False` 或 `None`。backend 抛出的非 SDK 异常会归一化为稳定 transport
错误。

自定义 `HandBackend.dispatch()` 必须在第一次 transport 发送前立即调用 `claim_dispatch()`。
返回 `False` 时应无 I/O 返回；返回 `True` 后只有实际发送成功才能调用 `mark_dispatched()`。
后端不能把入队、写入另一个异步队列或等待 ACK 描述为 transport 已发送。
