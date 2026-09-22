# HandDevice API

`HandDevice` 是七种灵巧手唯一的公开设备类。公共方法集合固定，型号差异通过
`DeviceCapabilities` 表达；不支持的调用抛出 `UnsupportedOperationError`，不会退回到
型号 manager 或猜测协议行为。

## 构造

```python
from linkerbot_hand import DeviceConfig, HandDevice

hand = HandDevice(
    model="l6",
    side="left",
    transport=transport,
    config=DeviceConfig(),
)
```

主要参数：

| 参数 | 含义 |
| --- | --- |
| `model` | `l6`、`o6`、`l20lite`、`l20`、`o20`、`l30` 或 `o30`，不区分大小写；`l20-lite` 是 `L20Lite` 的拼写别名 |
| `side` | 有左右手的型号必须为 `left` 或 `right`；L30 必须省略 |
| `transport` | 实现 `linkerbot-transport` 消息 transport 契约的对象 |
| `config` | 可选 `DeviceConfig`；控制队列容量、超时、关闭和恢复策略 |
| `clock` | 可选虚拟时钟，供确定性测试注入 |
| `protocol_backend` | 可选协议后端，供协议/runtime 隔离测试 |

`HandDevice` 与 `get_declared_capabilities(model, side=...)` 使用相同的型号规则。
历史名称 `o30i`、`l25`（包括其大小写变体）均不是配置别名，会抛出 `ValidationError`；
应分别使用 `o30`、`l20`。设备上报的产品编号由身份解析器单独识别，历史产品编号仍可
兼容并原样保留在 `product_code` 中，详见[发现文档](hand-discovery.md#探测顺序)。

构造只做参数校验并创建不可变初始状态，不打开 socket、不读取身份、不启动线程、不发
查询帧，也不产生运动。`runtime_factory` 是内部测试接缝，不是应用扩展协议的入口。

### 身份响应超时

`DeviceConfig.identity_timeout_s` 默认 `0.2` 秒，作用于 `open()`、`recover()`、
自动重连和 `get_device_info()` 的每个身份响应阶段。计时从该阶段整批请求发送完成开始，
完整响应提前收齐即结束等待；发送间隔和迟到响应隔离仍按协议执行。

L6/O6/L20Lite/L20 的 C0/C1/C2/C4 使用一个收集窗口（L6 发送 `C1 01`）；
L30 的两个阶段和 O30 的每个动态身份阶段分别使用该配置，不以一次阶段超时代替整条
身份链的总时间。`open()` 的外层预算按实际执行阶段的响应窗口与协议等待累加，并保留
`shutdown_timeout_s` 的调度/初始化余量，不再按遥测通道数量估算身份耗时。

`get_device_info(timeout)` 与 `recover(timeout)` 的参数仍是调用者等待结果的预算，
不会覆盖身份阶段窗口；有限恢复重试与执行预算见下文生命周期说明。

### 普通响应超时

`DeviceConfig.transaction_timeout_s` 默认 `None`，沿用各型号和功能定义的响应窗口；
显式设置有限正数时，覆盖初始化 baseline、主动 getter、轮询、写命令及离散操作中每个
需要响应的阶段。该配置不覆盖身份阶段，也不让无需回复的命令新增 ACK 等待。

```python
config = DeviceConfig(
    identity_timeout_s=0.3,       # 每个身份阶段最多等 300 ms
    transaction_timeout_s=None,  # 普通事务沿用各协议默认窗口
)
# 如需统一延长普通响应等待，可显式设置 transaction_timeout_s=0.4。
```

普通阶段也从整批请求发送完成开始计时；完整回复提前到达即完成。配置只改变响应等待，
不会修改协议发送间隔、写后稳定时间、迟到帧隔离、轮询频率或额外发送请求。
较长窗口会延后丢帧时的失败判定；成功回复不需要等满窗口。写回复超时不证明写入未执行，
SDK 不自动重发。getter 的调用者等待超时与阶段超时独立，也不取消其他调用者或轮询
共享的读取。

响应截止按 SDK 接收路由记录的单调时间判定，截止时刻及以后收到的帧不能完成该事务。
窗口内已进入有界接收队列的回复，即使 actor 稍后才恢复调度，仍按顺序解析；及时帧耗尽
或遇到第一条已过截止的帧就报告超时，持续来帧不会延长响应窗口。关闭、恢复中断和
恢复执行总预算仍逐次检查，不受积压帧影响。这是 SDK 接收时间，不是设备测量时间或
硬件 CAN 时间戳，底层阻塞 I/O 和协议解析也不能被 Python 强制抢占。

每个阶段只把发送仲裁器认领该批次之后收到的帧交给当前响应收集器。排队期间或认领前
已收到的旧帧按独立上报处理，不会用于新请求的确认、配置回显或多帧拼接。正常回复可能
在整个批次发送完之前到达，SDK 会暂存并在发送成功后发布；发送失败不会发布这些暂存
结果。此检查不要求设备返回值等于写入值，也不增加请求帧。对于没有请求序号的协议，
旧回复若迟到至下一批次已被认领之后，单靠时间无法完全区分；既有迟到隔离仍然保留。

## 生命周期

`DeviceConfig.recovery_timeout_s` 默认 `5.0` 秒，限制一轮恢复的准备、身份和 baseline
读取。达到截止后不开始新的协议阶段，也不提交有效的新连接代次。底层 transport 的同步
`recover()`/已认领发送无法被 Python 强制抢占：若它们阻塞超过预算，SDK 在返回后判定
恢复失败，不继续初始化。`close()` 可以打断 SDK 自身的响应和隔离等待。

`recover(timeout)` 是每个调用者独立的等待预算，从调用入口开始，包含获取生命周期锁
和发送排空。并发调用共享同一轮恢复；较短等待超时不会取消其他调用者的恢复，也不会
延长执行预算。一次失败后的后台行为由 `auto_reconnect` 决定；开启时可另起新一轮，
每轮有独立预算，不代表后台重连永远只有固定的总次数。

每轮恢复默认最多执行 `recovery_max_attempts=3` 次只读初始化（包含首次），仅重试身份
或 baseline 的协议响应超时。每次创建新 collector/事务链并重新验证完整身份和 baseline；
失败尝试中的部分样本保持不可见，成功只提交一次新 generation。身份不匹配、解析错误、
底层发送/传输失败不在此重试范围；写入和旧运动目标不重发。首次 `open()`、主动 getter
和发现查询也不使用此恢复重试策略。首轮正常完成时不增加等待或 CAN 帧。

重试复用本轮已经恢复的物理通道，不再次断开共享总线。开始新一轮查询前，丢弃本设备
路由收到的旧帧，等待一个完整静默窗口：取 `recovery_retry_delay_s`（默认 `0.2` 秒）、
失败阶段的响应窗口和协议迟到隔离窗口三者中的最大值。每次收到本设备的帧都会重新
计时；持续来帧也不会突破恢复总预算。

没有请求序号的协议无法绝对区分“超过静默窗口才到达的旧回复”和“新请求的回复”。
此策略隔离已缓存及窗口内到达的帧，不提供任意延迟下的轮次证明；现场若存在更长延迟，
应增大静默间隔和总预算，或设置 `recovery_max_attempts=1` 关闭本轮重试。其他设备的
路由不参与此静默判断。底层 transport 故障仍走原来的整通道恢复。

```python
config = DeviceConfig(
    identity_timeout_s=0.3,
    recovery_max_attempts=3,     # 最多三次只读初始化，包含首次
    recovery_timeout_s=5.0,     # 整轮恢复预算；调用者 timeout 仍独立
    recovery_retry_delay_s=0.2, # 重试前至少等本设备连续静默 200 ms
)
```

```python
hand.open()
try:
    ...
finally:
    hand.close()
```

| 方法 | 行为 |
| --- | --- |
| `open()` | 幂等；打开 transport、读取并验证身份、选择 firmware profile、读取 baseline |
| `close()` | 幂等；停止接收新任务、停止轮询、清空旧命令并在有界时间内关闭 |
| `start_polling(plan)` | 安装或原子替换轮询计划；相同计划重复调用为 no-op |
| `stop_polling()` | 取消未来周期查询；重复调用和关闭状态调用为 no-op |
| `recover(timeout)` | 显式重新连接、重新验证身份并读取 baseline；不重放运动目标 |

`HandDevice` 不实现上下文管理器，必须显式调用 `close()`。资源释放不依赖
`__del__`。

构造后、`open()` 前可以读取 declared capability：

```python
declared = hand.get_capabilities()
assert not declared.identity.verified

hand.open()
verified = hand.get_capabilities()
assert verified.identity.verified
```

型号不匹配、左右手不匹配或恢复时设备指纹变化都会以稳定异常失败。型号可选查询不由
版本号预先阻止；已发送查询若证明当前固件不支持，只降级该通道并保留连接。

## 非阻塞命令

所有 setter 在调用线程中只进行 capability 和数值校验，然后写入有界调度器并快速返回
`CommandHandle`：

| 方法 | 输入语义 |
| --- | --- |
| `set_angles(values)` | 精确角度协议值 |
| `set_percent_angles(values)` | `0..100`，按逐关节固定 capability 映射 |
| `set_speeds(values)` | 配置速度协议值 |
| `set_percent_speeds(values)` | 仅 profile 明确提供速度百分比映射时支持 |
| `set_accelerations(values)` | 配置加速度协议值 |
| `set_current_limits(values)` | 已确认表示电流上限的协议值 |
| `set_position_kp(values)` / `set_position_kd(values)` | L6 位置控制增益原值；Kp 默认 50、实际为 raw × 10，Kd 默认 21 |
| `set_torque_kd(values)` | L6 力矩模式的 Kd 原值，默认 1 |
| `set_torques_limits(values)` | 转矩限制协议原值（O30） |
| `set_torques(values)` | L6 `0x03` 力矩出力及模式：每关节 0 切位置模式，1..255 切力矩模式并设置出力；原始值不换算为 mA 或 N·m |
| `set_motion_times(values)` | 配置运动时间协议值 |
| `set_stall_detection_times(values)` | 堵转判定时间对象的协议值 |
| `set_stall_thresholds(values)` | 堵转阈值对象的协议值 |
| `set_stall_hold_currents(values)` | 堵转后维持电流对象的协议值 |
| `set_limit_compensations(values)` | 逐关节限位补偿协议值 |
| `clear_faults()` / `enable()` / `disable()` / `calibrate()` / `save_parameters()` | 独立离散操作 |

长度、有限数、整数约束和协议范围都来自 capability。SDK 不自动 clamp；校验失败直接抛
`ValidationError`，不会创建 handle。

角度、速度、电流上限和转矩上限各有独立容量为 1 的 latest slot。新目标会
把同命令族尚未被 transport claim 的旧目标标记为 `SUPERSEDED`。加速度、运动时间、堵转
配置、限位补偿（`CommandFamily.LIMIT_COMPENSATION`）、力矩出力/模式
（`CommandFamily.TORQUE_TARGET`）、控制增益和离散操作共用有界 FIFO，
容量由 `DeviceConfig.discrete_queue_capacity` 控制。已接纳的 FIFO 配置按顺序处理，
不会因同类新配置到来而标记为 `SUPERSEDED`；队列满时新提交抛出 `QueueFullError`，
不创建 handle。

### CommandHandle

```python
handle = hand.set_angles(target)

print(handle.command_id)
print(handle.stage)  # ACCEPTED
print(handle.ack_support)
print(handle.history)

# 只能在非实时、非 ROS callback 线程按需等待。
terminal = handle.wait(timeout=0.2)
```

| 阶段 | 精确定义 |
| --- | --- |
| `ACCEPTED` | 参数有效，生命周期允许，已进入有界调度器 |
| `DISPATCHED` | transport 已成功发送该命令的全部请求帧 |
| `ACKNOWLEDGED` | 收到该命令声明的完整有效协议回复；数值型配置回复不要求等于发送值 |
| `FAILED` | 接受后发生编码、发送、协议、超时或连接错误 |
| `SUPERSEDED` | transport claim 前被同命令族的新目标覆盖，确认未发送 |

不支持设备 ACK 的型号以 `DISPATCHED` 作为成功终态。SocketCAN send 成功、CAN ACK bit、
设备协议 ACK 和机械到位是四个不同概念。

数值型 `set_*()` 接受回复时不比较发送值与返回值是否相等。回复可能为当前值、旧值或
设备量化值；SDK 保留实际原值，不用目标值替换，不自动补偿或重写。
`ACKNOWLEDGED` 不证明请求值已原样应用；协议定义的失败状态码仍按错误处理。
地址、功能码、帧结构和多帧完整性仍须符合对应协议。

setter 返回 `CommandHandle`，设备样本通过对应快照字段读取；快照表示最新值，可能被
后续事务更新，不是该 handle 独占的回复。配置样本仍按型号声明的配置语义发布，
不因与请求值不同就改称实时测量；没有写回复发布的命令仍按其能力契约处理。

O6 的 `set_current_limits()` 写入限制并返回设定值：有效回复更新
`snapshot.current_limits`，来源为 `DEVICE_CONFIG_RESPONSE`，不更新实测电流。
主动读取当前电流用 `get_currents()`；`get_current_limits()` 只返回本连接最近的
写回复缓存，不发送查询；未收到有效写回复或重连后抛出 `UnsupportedOperationError`。

## 主动读取

以下 getter 接受正的秒制 `timeout`。由设备查询支持的 getter 返回调用开始之后的新样本；
若等价事务已在途，调用者会加入同一事务而不是再发查询。声明为 `COMMAND_CACHE` 的
getter 返回已发送命令形成的缓存样本；`COMMAND_RESPONSE` 返回设备对写命令的最近响应；
二者都不产生主动查询：

| 方法 | 返回类型 |
| --- | --- |
| `get_angles(timeout)` | `AngleSample` |
| `get_speeds(timeout)` | `MeasuredSpeedSample` |
| `get_configured_speeds(timeout)` | `ConfiguredSpeedSample` |
| `get_configured_accelerations(timeout)` | `ConfiguredAccelerationSample`；L20Lite 仅读取完整确认的写回显缓存，无主动查询 |
| `get_current_limits(timeout)` | `CurrentLimitSample` |
| `get_torques_limits(timeout)` | `TorqueLimitSample` |
| `get_torques(timeout)` | `TorqueSample`；L6 为 `0x03` 力矩出力/模式原值，O30 为实测转矩字段；是否声明实测见 capability 的 `device_measured` |
| `get_currents(timeout)` | `CurrentSample` |
| `get_position_kp(timeout)` / `get_position_kd(timeout)` | `PositionKpSample` / `PositionKdSample`；L6 原始位置控制增益 |
| `get_torque_kd(timeout)` | `TorqueKdSample`；L6 原始力控增益 |
| `get_temperatures(timeout)` | `TemperatureSample` |
| `get_force(*fields, timeout=1.0)` | `ForceSample`，默认可用 fn+fns；小写字段选择、原始值及各字段独立时间戳，见[触觉接口](./force.md) |
| `get_normal_force_resultant(timeout)` | `NormalForceResultantSample`；当前仅 O30，五指设备 FnS 原值及各自单位 |
| `get_faults(timeout)` | `FaultSample` |
| `get_voltages(timeout)` | `VoltageSample` |
| `get_motion_times(timeout)` | `MotionTimeSample` |
| `get_device_info(timeout)` | `DeviceInfo`，重新读取并验证身份 |
| `get_stall_detection_times(timeout)` | `StallDetectionTimeSample` |
| `get_stall_thresholds(timeout)` | `StallThresholdSample` |
| `get_stall_hold_currents(timeout)` | `StallHoldCurrentSample` |
| `get_limit_compensations(timeout)` | `LimitCompensationSample` |

某些配置状态由 capability 声明为 `COMMAND_CACHE`，另一些是
`COMMAND_RESPONSE`。例如 L6 配置速度来自设备 `0x05` 写响应，标记为
`DEVICE_CONFIG_RESPONSE`；它既不是本地主机目标，也不会伪装成设备实测速度。

`ForceSample.raw`、`tactile`、`sensors` 分别承载标量、矩阵、descriptor 触觉，按 force
capability 的 `payload_projection` 判断，不能由关节数量或 `raw_encoding` 猜测。
O30 的 `sensors` 保留五指身份、设备 metadata、完整 raw_data 与解码字段；使用说明见
[O30 触觉与合力](./models/o30.md#触觉与合力)。

ROS command、publisher 和 diagnostics callback 不应调用主动 getter。ROS 应让 SDK
轮询，并从 snapshot 发布。

## 角度反馈与轮询

```python
from linkerbot_hand import AngleFeedbackPolicy, PollingPlan

hand.start_polling(
    PollingPlan(
        angles_hz=100,
        speeds_hz=30,
        force_hz=30,
        temperature_hz=1,
        current_hz=10,
        faults_hz=5,
        torques_hz=0,
        voltage_hz=0,
        angle_feedback=AngleFeedbackPolicy.ADAPTIVE,
    )
)
```

角度设置返回当前角度是一类跨型号事件，不是 O6 或 L6 特例，但并非每份已确认协议都提供
这种返回。例如 L30 批量位置写明确无应答，O20 目标位置寄存器写也不能假设会返回当前
位置；O30 的 `MI 0x01, RTS=0` 角度写响应同时确认命令并返回当前角度，可供反馈复用。
该返回可能与目标不同，不表示机械到位。因此每个型号的
command capability 必须用 `returns_current_state` 明确声明，协议 profile 负责关联和完整
解码；公共 runtime 只消费已经验证并标记为 `DEVICE_COMMAND_FEEDBACK` 的当前角度样本，
不假设 O6 的 `0x01 + 6 byte` 格式，也不把目标值回显或 ACK 冒充当前状态。

`PollingPlan.angle_feedback` 只影响 `angles_hz` 的周期反馈来源：

| 策略 | 行为 |
| --- | --- |
| `ADAPTIVE` | 默认。经 capability 验证的当前角度返回满足采样 deadline；没有可用返回或数量不足时只对缺口主动查询 |
| `QUERY_ONLY` | 周期频率全部由主动查询完成；角度命令反馈仍写快照，但不抑制查询 |
| `COMMAND_FEEDBACK_ONLY` | 仅适用于 `returns_current_state=True`；不产生周期角度查询，只用应用实际提交目标后形成的设备当前角度返回；缺失周期记为 missed |

若型号没有经验证的当前状态写返回，配置 `COMMAND_FEEDBACK_ONLY` 会在发送任何帧前抛出
`PollingPlanError`。`ADAPTIVE` 仍可跨全系列使用，此时该型号自然退化为主动查询。

SDK 不要求也不接受一个虚构的“目标发送频率”，不会按频率自行重复最后运动目标。
`DeviceConfig.command_rate_hz` 只是调度发送上限。应用仍按自己的控制循环调用
`set_angles()` / `set_percent_angles()`；自适应调度根据实际收到的命令反馈决定是否补询。

`get_angles(timeout)` 的语义不受反馈策略影响：它始终请求调用开始后的显式主动查询样本，
不会被偶然到达的角度命令反馈完成。

频率为 0 的通道不产生查询帧。`speeds_hz` 专指设备实测速度，不会轮询配置速度。
计划包含不支持通道时，`start_polling()` 在发送前抛出
`UnsupportedOperationError`。SDK 不对正频率设置静态上限；应用应根据实机完成率、
missed deadline、timeout 和总线健康状态确定可持续频率。

轮询 tick 不积累历史任务。前一事务尚未完成时，新 tick 会合并或记为 missed deadline，
不会形成无界查询队列。

## 快照

`get_snapshot()` 只读取 copy-on-write 不可变缓存，不调用 protocol 或 transport：

```python
snapshot = hand.get_snapshot()
angles = snapshot.angles

if angles is not None and angles.meta.valid and not angles.meta.stale:
    print(angles.raw)
    print(angles.percent)
    print(angles.meta.sequence)
    print(angles.meta.collected_at)
    print(angles.meta.connection_generation)
    print(angles.meta.origin)
```

`HandSnapshot` 包含：

- `captured_at`、`snapshot_sequence`、`connection_generation`；
- capability 顺序的 `joints` 便捷视图；
- 缓存的 `device_info`；
- 角度、实测/配置速度、配置加速度、电流、限制值、温度、触觉、故障、电压、
  运动时间和堵转配置等可选类型化样本。

每个样本有独立 `SampleMeta`：

| 字段 | 含义 |
| --- | --- |
| `sequence` | 该通道成功发布次数 |
| `collected_at` | monotonic 采集时间 |
| `age_s` | 读取快照时的数据年龄 |
| `valid` / `stale` | 当前连接代次内是否有效、是否过期 |
| `connection_generation` | 样本所属连接代次 |
| `origin` | 设备响应、主动上报、配置响应、回显或命令缓存 |
| `within_declared_range` | 反馈是否落在 capability 声明范围 |

断线或代次变化后，旧样本立即失效。反馈超出声明范围时保留真实 raw，不静默 clamp。

专用状态消费线程可等待缓存变化，避免按固定周期扫描时与设备反馈发生同频混叠：

```python
previous = hand.get_snapshot()
while running:
    current = hand.wait_for_snapshot(previous, timeout=0.05)
    if (
        current.connection_generation,
        current.snapshot_sequence,
    ) != (
        previous.connection_generation,
        previous.snapshot_sequence,
    ):
        consume(current)
    previous = current
```

`wait_for_snapshot(previous, timeout)` 只等待 copy-on-write 缓存的连接代次或 sequence
变化，不调用 protocol 或 transport。超时后返回重新计算 age/stale 的当前缓存，因此返回值可能
与 `previous` 具有相同 revision。该方法是明确的阻塞接口，只应在专用消费线程使用；命令、
ROS subscription、timer 和 diagnostics callback 仍应调用非阻塞 `get_snapshot()`。

只消费主动查询状态的发布线程可传入
`wake_on_command_feedback=False`。设备命令反馈仍正常写入快照和健康指标，但只由命令反馈
产生的 revision 不会唤醒该线程；其他通道、主动查询、连接失效和连接代次变化仍会唤醒。

## 健康状态

`get_health()` 只读内存，至少报告：

- 生命周期、连接状态、是否已连接、是否轮询；
- 身份验证状态、连接代次、重连次数；
- 最近成功发送、接收和完整事务时间；
- 连续错误数和结构化最近错误；
- 各命令族 pending/accepted/dispatched/acknowledged/failed/superseded；
- 离散 FIFO 深度；
- 各状态 requested/achieved Hz、当前 plan 的 `completed_reads`、`command_feedback_samples`、
  `feedback_samples`、反馈策略、missed deadline、timeout、数据年龄和最新缓存来源；
- transaction P50/P95/P99；
- transport TX/RX 帧与字节、丢帧、route miss、bus error 和队列深度。

调用 `get_health()` 不会为了“刷新健康”发送故障查询。新故障数据只能来自显式
`get_faults()` 或 `PollingPlan.faults_hz`。

`ChannelMetrics.completed_reads` 每完成一个逻辑读取事务增加一次；多帧触觉完整事务只增加
一次。相同 plan 重复启动不会清零，替换 plan 或 `stop_polling()` 后归零。缓存
`DataAge.sequence` 则在任何新样本发布时增加，因此设备 report/command feedback 可以增加
sequence，但不会增加 polling 读取完成数。`DataAge.origin` 给出该通道最新缓存样本的来源。

角度通道的 `command_feedback_samples` 统计当前 plan 内已验证的角度命令反馈；
`feedback_samples` 统计按当前策略真正满足反馈频率的查询或命令反馈总数；`achieved_hz`
由 `feedback_samples` 计算。因此在 `QUERY_ONLY` 下命令反馈会增加
`command_feedback_samples` 和快照 sequence，但不会增加 `feedback_samples`。

`health.transport.tx_frames`、`rx_frames`、`tx_bytes` 和 `rx_bytes` 是整条物理通道跨连接
代次累计的流量，不是单个 HandDevice 的流量。同一 CAN/CAN FD 通道上的多个设备会看到
相同计数。字节数按 DLC 对应的数据区长度统计，不含仲裁、CRC、位填充和帧间隔，因此不能
直接当作总线利用率。单设备行为应结合该设备的 command family、读取完成数和数据通道指标
判断。

## 安全恢复

`DeviceConfig(auto_reconnect=True)` 启用自动重连；也可显式调用 `recover(timeout)`。
恢复会重新验证型号、固件、传感器变体和设备指纹，清除旧命令，读取当前 baseline，
然后恢复轮询计划。

恢复明确禁止：

- 重放断线前或断线期间目标；
- 自动全开或握拳；
- 自动清故障；
- 自动 enable 或 calibrate。

## 稳定异常

公开异常都继承 `HandError`，并携带稳定 `ErrorCode`、operation、model、generation 和
原始 cause。主要类型：

- `ValidationError`
- `UnsupportedOperationError`
- `UnsupportedFirmwareError`
- `IdentityMismatchError`
- `DeviceNotReadyError`
- `TransportError`
- `ProtocolError`
- `TimeoutError`
- `DeviceFaultError`
- `QueueFullError`
- `PollingPlanError`
- `ClosedError`
- `LifecycleError`

上层应映射异常类型或 `error.code`，不要解析错误字符串。
