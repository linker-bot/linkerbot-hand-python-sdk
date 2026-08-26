# 电流读取

L6 当前电流反馈是 6 个 `0..255` 协议字节。默认 manager 与统一快照都返回协议整数，
不隐式换算为 mA。

```python
from linkerbot.hand import L6

with L6(side="left", interface_name="can0") as hand:
    data = hand.current.get_blocking(timeout_ms=500)
    print(data.currents)  # tuple[int, ...]

    cached = hand.current.get_snapshot()
    if cached is not None:
        print(cached.currents)
```

关节顺序为拇指弯曲、拇指侧摆、食指、中指、无名指、小指。事件流中的
`CurrentEvent.data.currents` 使用相同协议值。

协议文档给出的满量程约为 1400 mA。需要物理单位时显式使用 `L6Current`：

```python
from linkerbot.hand.l6 import L6Current

milliamps = L6Current.from_raw(list(data.currents)).to_list()
print(milliamps)
```

统一 `SensorSample` 对该通道标注 `unit="count"`、`encoding="u8"`、
`physical_unit="mA"`、`scale=1400/255`、`offset=0`。`values` 仍是协议整数，不能直接
标成 mA。
