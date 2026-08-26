# 统一协议值接口迁移说明

本版本把型号 manager 与 `HandDevice` 的默认数值契约统一为协议值。这是有意的破坏性
变更，不保留旧的用户角度映射行为。迁移目标是让设备控制、遥操采集、回放和模型训练
使用同一份数据，不再依赖隐式换算。

## 导入与创建

统一类型从 `linkerbot_hand` 导入，内置硬件驱动通过 `create_hand_device()` 创建：

```python
from linkerbot.hand import create_hand_device
from linkerbot_hand import CommandStage, PollingPlan

hand = create_hand_device("L6", side="left", interface_name="can0")
hand.open()
```

构造不产生 I/O；必须显式 `open()`，并通过 `start_polling()` 启动查询。

## 数值接口对照

| 用途 | 型号 manager | 统一接口 | 数据语义 |
| --- | --- | --- | --- |
| 角度控制 | `angle.set_angles(values)` | `submit_angles(values)` | 协议位置值 |
| 百分比角度 | `angle.set_percentage_angles(values)` | `submit_percentage_angles(values)` | 显式 `0..100` |
| 速度控制 | `speed.set_speeds(values)` | `submit_speeds(values)` | 协议速度值 |
| 百分比速度 | `speed.set_percentage_speeds(values)` | `submit_percentage_speeds(values)` | 显式 `0..100`，能力受限 |
| 力矩/限制控制 | `torque.set_torques(values)` | `submit_torques(values)` | 协议值 |
| 状态读取 | `manager.get_blocking()` | `start_polling()` + `get_snapshot()` | 协议解码值 |

原来把 `set_angles()` 或 `set_speeds()` 当作百分比的调用点，必须改用带
`percentage` 的方法。已经持有设备协议值的调用点继续使用简短默认方法，不再增加
第二个 raw 命名入口。

型号 manager 的改名入口保留既有数值换算：四款经典型号线性映射到 `0..255`，O20 / L30
按逐轴最小值到最大值映射，O30i 使用反向字节映射。统一
`submit_percentage_angles()` 则使用 capability 的机械端点语义；两者在部分轴上方向不同。
跨入口迁移时先转换为协议值，不能只替换方法名后混用百分比数组。

百分比角度以 capability 的逐轴端点为准。百分比速度目前只由 L6、O6、L20Lite 和
L25 明确支持；L30、O20 和 O30i 会抛出 `UnsupportedOperationError`。特别注意，
O20 的协议速度目标恰好是 `0..100`，仍不能解释成百分比。

## 状态数据迁移

`SensorSample.values` 现在是唯一数值载荷，始终表示协议解码值。读取时同时保存：

```python
sample = hand.get_snapshot().angles
if sample is not None and sample.valid:
    values = sample.values
    unit = sample.unit
    encoding = sample.encoding
```

`JointState.angle`、`speed` 和 `torque` 也直接来自这些协议样本。数据集应记录型号、
固件、关节顺序、范围、单位和编码，不能只保存一个无上下文的数值数组。

需要百分比或物理量时，在数据边界显式调用转换 helper。角度可使用
`percent_to_raw()` / `raw_to_percent()`；能力发布物理标定后才可使用
`raw_to_physical_angles()` / `physical_angles_to_raw()`。RPM 与 mA 的型号换算见
[协议值、百分比与显式换算](./angle-coordinates.md)。

## ROS Lifecycle 映射

| ROS 阶段 | SDK 操作 |
| --- | --- |
| `on_configure` | 创建 `HandDevice`，读取静态 capability，不打开设备。 |
| `on_activate` | `open()`，再启动 `PollingPlan`。 |
| subscription callback | 调用 `submit_*()` 后立即返回，不调用 `wait()`。 |
| publisher timer | 只读取 `get_snapshot()` / `get_health()`。 |
| `on_deactivate` | `stop_polling()`，不自动发送动作。 |
| cleanup / shutdown | `close()`。 |

ROS 不应维护第二份型号范围或换算表。协议值 topic 与百分比 topic 必须使用不同名称；
百分比 topic 只能调用显式 percentage 方法。禁用 SDK 轮询后，只读取快照不会产生查询帧。

## 命令完成语义

`submit_*()` 返回 `CommandHandle`。不支持设备 ACK 的命令以 `DISPATCHED` 为成功终态；
只有 capability 的 `acknowledged_commands` 明确列出的命令才可能进入
`ACKNOWLEDGED`。机械到位需要根据协议反馈另行判断。

```python
handle = hand.submit_angles(protocol_target)
if handle.stage is CommandStage.SUPERSEDED:
    dropped_targets += 1
```

## 建议迁移顺序

1. 从 capability 读取关节顺序、命令范围、单位、编码和传感器支持。
2. 将协议值调用保留在默认接口，将百分比调用改成显式 percentage 接口。
3. 将状态消费者改为读取 `SensorSample.values/unit/encoding` 和 `JointState.angle`。
4. 删除应用侧的隐式百分比、RPM、mA 和逐型号角度映射。
5. 把周期查询交给 `PollingPlan`，发布线程只读缓存。
6. 用 fake backend 验证协议值逐项不变，再按 capability 顺序做实物往返验证。

断线后由 `recover()` 或自动重连替换并验证连接。恢复会清除尚未发送的旧目标，不会
重放动作、自动张开、握拳或清除故障。
