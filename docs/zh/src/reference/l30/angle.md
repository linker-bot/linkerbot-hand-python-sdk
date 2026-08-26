# 角度控制与读取

L30 的默认角度接口直接收发 17 个协议位置整数，顺序为 `J1..J17`。SDK 不在
`set_angles()`、`get_blocking()` 或快照路径中执行百分比、度或弧度换算。

## 协议值控制

每个位置必须是 Python `int`，并落在对应关节的 `L30_JOINT_SPECS` 范围内：

```python
from linkerbot.hand import L30

target = [
    450,
    600,
    450,
    400,
    0,
    750,
    800,
    800,
    750,
    800,
    750,
    0,
    0,
    0,
    800,
    750,
    0,
]

with L30() as hand:
    hand.angle.set_angles(target)
```

协议值域包含 `0..900`、`0..1600`、`-200..200` 和 `-1000..1000` 等逐轴范围，
不能用一个全局范围代替。完整表见 [L30 关节说明](./README.md#关节说明)。数量、类型或
逐轴范围不正确时，方法在发帧前抛出 `ValidationError`。

## 显式百分比控制

型号 manager 提供名称明确的百分比入口：

```python
with L30() as hand:
    hand.angle.set_percentage_angles([50.0] * 17)
```

这个 helper 保留 L30 的逐轴数值约定：`0` 映射到 `spec.minimum`，`100` 映射到
`spec.maximum`。对称轴的 `50` 因而映射到 0。`L30Angle.from_list()`、`from_raw()` 和
`to_raw()` 提供相同的显式纯换算，不参与默认控制或反馈路径。

统一接口 `HandDevice.submit_percentage_angles()` 使用 capability 中的机械端点语义：
屈伸轴为 `0=弯曲、100=伸直`，方向轴使用各自的 `zero_endpoint` 和
`hundred_endpoint`。它与上述型号 manager 的“最小值到最大值”规则在部分轴上方向不同，
两类百分比数据不能混用。需要无损采集、回放或训练时应保存并提交协议值。

## 读取与快照

```python
with L30() as hand:
    data = hand.angle.get_blocking(timeout_ms=1000)
    print(data.angles)  # tuple[int, ...]，17 个协议位置值
    print(data.timestamp)

    cached = hand.angle.get_snapshot()
    if cached is not None:
        print(cached.angles)
```

`get_blocking()` 读取当前位置并更新 manager 缓存；`get_snapshot()` 只返回最近缓存，不发
请求。传感器偶尔可能报告超出命令范围的校准或超调值，读取路径保留该协议值，不裁剪也不
归一化。

统一 `HandDevice` 快照中的同一数据位于 `snapshot.angles.values`，并标注：

- `unit="count"`
- `encoding="i16_be"`
- `physical_unit=None`、`scale=None`、`offset=None`

L30 当前没有可靠的物理角度标定，因此 `raw_to_physical_angles()` 和
`physical_angles_to_raw()` 会抛出 `UnsupportedOperationError`。

## API 摘要

| API | 输入或返回 | 语义 |
| --- | --- | --- |
| `set_angles(values)` | 17 个逐轴范围内的 `int` | 直接发送协议位置 |
| `set_percentage_angles(values)` | 17 个 `0..100` 数值 | 显式按 `minimum -> maximum` 换算 |
| `get_blocking(timeout_ms)` | `AngleData` | 读取协议位置并更新缓存 |
| `get_snapshot()` | `AngleData | None` | 只读 manager 缓存 |
| `L30Angle.to_raw()` | 17 个协议整数 | 显式百分比 helper |

统一接口的协议值、百分比和物理量边界见
[协议值、百分比与显式换算](../angle-coordinates.md)。
