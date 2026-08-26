# 角度控制与读取

O20 的默认角度接口直接收发 16 个有符号协议整数，顺序为 motor ID 1..16。该协议字段
本身以度为单位，因此“协议值”和“度数”数值相同；SDK 不再额外归一化为 `0..100`。

## 协议值控制

```python
from linkerbot.hand.o20 import O20

target = [
    60,
    75,
    90,
    65,
    0,
    90,
    90,
    0,
    90,
    90,
    0,
    90,
    90,
    0,
    90,
    90,
]

with O20(side="right") as hand:
    hand.angle.set_angles(target)
```

每个值必须是 Python `int`，并落在对应的 `O20_JOINT_SPECS` 范围内。四个外展轴包含
负值，其它轴也有不同上限，不能按统一 `0..180` 校验。完整范围见
[O20 关节说明](./README.md#关节说明)。

## 显式百分比控制

型号 manager 提供名称明确的百分比入口：

```python
with O20(side="right") as hand:
    hand.angle.set_percentage_angles([50.0] * 16)
```

这个 helper 按每轴 `spec.minimum -> spec.maximum` 线性换算：`0` 是最小值，`100` 是
最大值；四个对称外展轴的 `50` 是 0 度。`O20Angle.from_list()`、`from_raw()` 和
`to_raw()` 提供相同的显式纯换算，不参与默认控制或反馈路径。

统一接口 `HandDevice.submit_percentage_angles()` 使用 capability 的机械端点语义：屈伸轴
为 `0=弯曲、100=伸直`，方向轴使用各自声明的端点。因此它与型号 manager 的
“最小值到最大值”规则在部分轴上方向不同。需要跨入口复用数据时使用协议值，不要把两类
百分比数组直接互换。

## 读取与快照

```python
with O20(side="right") as hand:
    data = hand.angle.get_blocking(timeout_ms=1000)
    print(data.angles)  # tuple[int, ...]，16 个协议度数
    print(data.timestamp)

    cached = hand.angle.get_snapshot()
    if cached is not None:
        print(cached.angles)
```

`get_blocking()` 读取当前位置并更新 manager 缓存；`get_snapshot()` 只返回最近缓存，不发
请求。传感器可能因标定或超调报告略超出命令范围的值，读取路径保留该有符号协议值，不裁剪
也不归一化。

统一 `HandDevice` 快照中的同一数据位于 `snapshot.angles.values`，并标注：

- `unit="degree"`
- `encoding="i16_le"`

O20 capability 已确认协议位置单位就是度，因此 `raw_to_physical_angles()` 与
`physical_angles_to_raw()` 是显式、可逆的物理角度 helper；默认路径仍直接返回协议值。

## API 摘要

| API | 输入或返回 | 语义 |
| --- | --- | --- |
| `set_angles(values)` | 16 个逐轴范围内的 `int` | 直接发送协议位置，单位度 |
| `set_percentage_angles(values)` | 16 个 `0..100` 数值 | 显式按 `minimum -> maximum` 换算 |
| `get_blocking(timeout_ms)` | `O20AngleData` | 读取协议位置并更新缓存 |
| `get_snapshot()` | `O20AngleData | None` | 只读 manager 缓存 |
| `O20Angle.to_raw()` | 16 个协议整数 | 显式百分比 helper |

统一接口的协议值、百分比和物理量边界见
[协议值、百分比与显式换算](../angle-coordinates.md)。
