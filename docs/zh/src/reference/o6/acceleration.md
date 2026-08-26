# 加速度控制与读取

O6 加速度在线上使用 6 个 `0..254` 协议整数。`1..254` 依次增大，`0` 是协议定义的
最大加速度特殊值。默认控制和反馈保留该编码，不自动换算为百分比或度/秒²。

```python
from linkerbot.hand import O6

with O6(side="left", interface_name="can0") as hand:
    hand.acceleration.set_accelerations([128] * 6)

    data = hand.acceleration.get_blocking(timeout_ms=500)
    print(data.accelerations)  # tuple[int, ...]，范围 0..254
```

需要逻辑 `0..100` 输入时使用显式方法；其中 `100` 按协议编码为特殊值 `0`：

```python
hand.acceleration.set_percentage_accelerations([80] * 6)
```

## 显式兼容换算

协议资料同时给出 `8.7 度/秒²`、另一条不一致的换算式，并规定 raw `0` 表示最大值，
因此不存在可发布为统一线性 `scale/offset` 的关系。`O6Acceleration` 保留既有的显式
兼容 helper：

```python
from linkerbot.hand.o6 import O6Acceleration

physical = O6Acceleration.from_raw(list(data.accelerations)).to_deg_per_sec2()
protocol_target = O6Acceleration.from_deg_per_sec2([1000.0] * 6).to_raw()
hand.acceleration.set_accelerations(protocol_target)
```

事件流中的 `AccelerationEvent.data.accelerations` 仍是协议整数。统一快照只保存
`SensorSample.values`，并标注 `unit="count"`、`encoding="u8"`、
`physical_unit="degree_per_second_squared"`，`scale/offset=None`；不存在第二份
`raw_values`，也不应把兼容 helper 结果当作已确认标定值。
