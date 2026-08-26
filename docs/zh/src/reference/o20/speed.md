# 速度控制与读取

O20 速度目标使用 16 个 `0..100` 协议整数，顺序为 motor ID 1..16。这个值域是协议本身
的范围，不代表百分比；`set_speeds()` 和统一 `submit_speeds()` 都直接发送该整数。

```python
from linkerbot.hand.o20 import O20

with O20(side="right") as hand:
    hand.speed.set_speeds([40] * 16)
    hand.speed.set_all(40)
```

O20 没有已确认的百分比速度映射，因此不提供型号 manager 的
`set_percentage_speeds()`；统一 `submit_percentage_speeds()` 也会抛出
`UnsupportedOperationError`。

## 协议值读取

当前速度反馈解码为 16 个有符号 `i16_le` 协议整数，不自动换算为 RPM：

```python
with O20(side="right") as hand:
    data = hand.speed.get_blocking(timeout_ms=1000)
    print(data.speeds)  # tuple[int, ...]

    cached = hand.speed.get_snapshot()
    if cached is not None:
        print(cached.speeds)
```

统一 `SensorSample` 标注 `unit="protocol_speed"`、`encoding="i16_le"`。

## 显式 RPM 换算

协议文档给出的比例为每单位 0.732 RPM：

```python
from linkerbot.hand.o20 import protocol_speeds_to_rpm, rpm_to_protocol_speeds

rpm = protocol_speeds_to_rpm(list(data.speeds))
protocol_values = rpm_to_protocol_speeds(list(rpm))
```

`rpm_to_protocol_speeds()` 可覆盖有符号反馈范围；若结果用于目标控制，仍必须满足
`set_speeds()` 的 `0..100` 命令范围。

## 参数校验

| 校验项 | 规则 |
| --- | --- |
| 数量 | 必须是 16 个值 |
| 类型 | 每个目标必须是 Python `int` |
| 目标范围 | 每个目标必须在 `0..100` |
