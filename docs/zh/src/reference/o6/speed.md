# 速度控制与读取

O6 的默认速度接口收发 6 个 `0..255` 协议整数。`set_speeds()`、`get_blocking()` 和
`get_snapshot()` 都不隐式换算为百分比或 RPM。

```python
from linkerbot.hand import O6

with O6(side="left", interface_name="can0") as hand:
    hand.speed.set_speeds([128, 128, 192, 192, 192, 192])

    data = hand.speed.get_blocking(timeout_ms=500)
    print(data.speeds)  # tuple[int, ...]
```

需要 `0..100` 输入时调用：

```python
hand.speed.set_percentage_speeds([50, 50, 75, 75, 75, 75])
```

## 显式兼容换算

完整协议资料同时写有 `0.732 RPM/count` 和 `raw * 180 / 255`，两种比例并不一致。
`O6Speed` 保留既有的 `raw * 180 / 255` 显式 helper，供兼容场景使用：

```python
from linkerbot.hand.o6 import O6Speed

rpm = O6Speed.from_raw(list(data.speeds)).to_rpm()
protocol_target = O6Speed.from_rpm([90.0] * 6).to_raw()
hand.speed.set_speeds(protocol_target)
```

事件流中的 `SpeedEvent.data.speeds` 仍是协议整数。统一快照标注 `unit="count"`、
`encoding="u8"`、`physical_unit="rpm"`；由于比例冲突，`scale/offset=None`。在固件侧
确认比例前，不应把 helper 结果当作已标定 RPM。
