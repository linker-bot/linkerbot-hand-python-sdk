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

## 显式 RPM 换算

O6 文档给出的换算为 `protocol * 180 / 255`。`O6Speed` 只作为显式转换 helper：

```python
from linkerbot.hand.o6 import O6Speed

rpm = O6Speed.from_raw(list(data.speeds)).to_rpm()
protocol_target = O6Speed.from_rpm([90.0] * 6).to_raw()
hand.speed.set_speeds(protocol_target)
```

事件流中的 `SpeedEvent.data.speeds` 仍是协议整数。统一快照标注
`unit="protocol_raw"`、`encoding="u8"`。
