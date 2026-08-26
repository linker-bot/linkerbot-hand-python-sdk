# 电流读取

L30 电流反馈是 17 个有符号大端 `i16_be` 协议值，顺序为 `J1..J17`。manager 和统一快照
都保留协议整数，不隐式换算为 mA，也不提供第二份 `raw_values`。

```python
from linkerbot.hand import L30

with L30() as hand:
    data = hand.current.get_blocking(timeout_ms=1000)
    print(data.currents)  # tuple[int, ...]

    cached = hand.current.get_snapshot()
    if cached is not None:
        print(cached.currents)
```

协议文档给出的比例为每单位 6.5 mA。需要物理量时显式调用纯转换函数：

```python
from linkerbot.hand.l30 import (
    milliamps_to_protocol_currents,
    protocol_currents_to_milliamps,
)

milliamps = protocol_currents_to_milliamps(list(data.currents))
protocol_values = milliamps_to_protocol_currents(list(milliamps))
```

统一 `SensorSample` 标注 `unit="count"`、`encoding="i16_be"`、
`physical_unit="mA"`、`scale=6.5`、`offset=0`。`values` 仍是协议计数。
