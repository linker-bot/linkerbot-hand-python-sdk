# 协议值、百分比与显式换算

`linkerbot-hand` 的默认数据契约是协议值。控制、反馈、缓存快照和 fake backend
使用同一套逐关节顺序和值域，不在主路径中自动归一化，也不自动换算为 RPM、mA、
度或弧度。这使遥操采集、回放和模型训练可以复用同一接口，避免重复换算。

## 默认接口

| 数据 | 型号 manager | 统一接口 | 语义 |
| --- | --- | --- | --- |
| 角度命令 | `set_angles(values)` | `submit_angles(values)` | 协议位置值 |
| 速度命令 | `set_speeds(values)` | `submit_speeds(values)` | 协议速度值 |
| 力矩命令 | `set_torques(values)` | `submit_torques(values)` | 协议力矩或电流限制值 |
| 传感器反馈 | `get_blocking()` / `get_snapshot()` | `get_snapshot()` | 解码后的协议值 |

范围、整数约束和关节顺序来自 `get_capabilities()`。统一快照只保留一份
`SensorSample.values`，并通过 `unit` 和 `encoding` 标注它的协议单位与线上编码。

```python
from linkerbot.hand import create_hand_device
from linkerbot_hand import PollingPlan

hand = create_hand_device("L6", side="left", interface_name="can0")
hand.open()
try:
    hand.start_polling(PollingPlan(angles_hz=60))
    hand.submit_angles([255, 128, 255, 255, 255, 255])

    sample = hand.get_snapshot().angles
    if sample is not None and sample.valid:
        print(sample.values)  # 协议 u8 值
        print(sample.unit)  # count
        print(sample.encoding)  # u8
finally:
    hand.stop_polling()
    hand.close()
```

## 样本元数据

`unit` 始终描述 `SensorSample.values`，而不是换算后的展示单位。原始协议计数统一使用
`count`；协议值本身已经是物理量时使用 `mA`、`degree_celsius` 等真实单位。线上编码：

- `u8`：无符号 8 位整数；
- `u16_le`：无符号 16 位整数，小端字节序；
- `i16_le`：有符号 16 位整数，小端字节序；
- `i16_be`：有符号 16 位整数，大端字节序。

`physical_unit` 表示对应物理量的单位。仅当 `scale` 与 `offset` 同时存在时，转换关系才
经过协议确认：

```text
physical = raw * scale + offset
```

若只有 `physical_unit`，表示物理维度或端点已知，但线性比例缺失或协议资料互相冲突，
不能据此换算。`value_kind` 区分 `numeric`、`bitmask`、`enum` 和
`tactile_matrix`；触觉的标量编码仍为 `u8`，每指行列数由 `matrix_shape` 单独声明。

## 显式百分比接口

需要无量纲 `0..100` 控制时，方法名必须明确包含 `percentage`：

- 型号 manager：`set_percentage_angles()`、`set_percentage_speeds()`。
- 统一接口：`submit_percentage_angles()`、`submit_percentage_speeds()`。
- 本地换算：`percent_to_raw()` / `raw_to_percent()`、
  `percentage_speeds_to_raw()` / `raw_speeds_to_percentage()`。

统一 `submit_percentage_angles()` 的屈伸轴使用 `0=弯曲/闭合，100=伸直/张开`。
侧摆、横滚、旋转和手腕轴的 `0` 与 `100` 仅表示 capability 中的 `zero_endpoint` 和
`hundred_endpoint`，不能解释为开合。

百分比角度必须有 `JointCapability.percentage_mapping`。百分比速度必须有
`percentage_speed_supported=True`；目前只有 L6、O6、L20Lite 和 L25 明确支持。
O20 的协议速度目标范围恰好是 `0..100`，但它仍是协议范围，不是百分比映射。

```python
hand.submit_percentage_angles([100, 50, 100, 100, 100, 100])
hand.submit_percentage_speeds([50] * 6)  # 仅能力声明支持的型号
```

### 型号 manager 的百分比规则

型号 manager 的显式 `set_percentage_angles()` 保留各协议驱动原有的数值换算，方便把旧
百分比调用改名后保持同一目标：

| 型号 | manager 的 `0..100` 换算 |
| --- | --- |
| L6 / O6 / L20Lite / L25 | 每轴线性映射到协议 `0..255` |
| O20 / L30 | 每轴从 `spec.minimum` 线性映射到 `spec.maximum` |
| O30i | 方向轴正向映射 `0 -> 0`、`100 -> 255`；屈伸轴反向映射 `0 -> 255`、`100 -> 0` |

O30i 的 manager 与统一 capability 使用相同逐轴端点。O20、L30 的 manager 规则在部分方向轴
或屈伸轴上仍与统一 capability 端点不同。
百分比数组必须连同入口及换算规则一起记录；跨 manager、统一接口、遥操和训练数据复用时，
应使用协议值。

## 显式物理量换算

物理量换算是纯函数或显式 helper，不会隐式应用在默认控制或反馈上：

| 型号/数据 | 显式换算 |
| --- | --- |
| 能力已标定的角度 | `raw_to_physical_angles()` / `physical_angles_to_raw()` |
| O6 速度 | `O6Speed.from_raw(...).to_rpm()` / `O6Speed.from_rpm(...).to_raw()`（兼容 helper，协议比例有冲突） |
| O20 速度 | `protocol_speeds_to_rpm()` / `rpm_to_protocol_speeds()` |
| L6/O6 电流限制 | 对应 torque helper 的 `from_raw()`、`to_milliamps()`、`from_milliamps()` |
| O20/L30 电流限制 | `protocol_current_limits_to_milliamps()` / `milliamps_to_protocol_current_limits()` |
| L30 电流反馈 | `protocol_currents_to_milliamps()` / `milliamps_to_protocol_currents()` |

能力没有可靠物理标定时，转换方法抛出 `UnsupportedOperationError`，不会把协议值
伪装成物理单位。O6 的旧物理 helper 仍可显式调用，但完整协议资料给出的比例互相冲突，
因此 capability 的 `scale/offset` 保持 `None`，不应把 helper 结果当作已确认标定值。

## 数据采集边界

遥操、回放和训练数据建议保存：

- 型号、固件、左右手和 `connection_generation`；
- capability 中的关节顺序、范围、`unit`、`encoding`、物理元数据和值语义；
- 命令协议值与 `SensorSample.values`；
- 采集时间、序号、有效性和过期状态。

百分比或物理量应作为带有明确转换版本的派生数据。不要用相同字段名混放协议值和
换算值，也不要把 O20 的协议 `0..100` 速度误标成百分比。

## ROS 2 接口边界

ROS 驱动应以协议值 topic 作为无损控制与状态接口：

| ROS 数据 | SDK 来源 |
| --- | --- |
| 协议角度命令 | `submit_angles()` |
| 协议速度命令 | `submit_speeds()` |
| 协议角度状态 | `snapshot.angles.values` |
| 协议速度状态 | `snapshot.speeds.values` |

若业务需要百分比 topic，应使用不同名称，并分别调用显式 percentage 方法或本地转换
helper。ROS 层不应再实现第二份逐型号换算表。
