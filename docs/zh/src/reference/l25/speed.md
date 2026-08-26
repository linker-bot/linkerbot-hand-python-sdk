# 速度控制与读取

L25 的速度接口按协议关节顺序收发 16 个 `0..255` 整数。默认路径不执行百分比换算。

```python
from linkerbot.hand import L25

with L25(side="left", interface_name="can0") as hand:
    hand.speed.set_speeds([128] * 16)

    data = hand.speed.get_blocking(timeout_ms=500)
    print(data.speeds)  # tuple[int, ...]
```

需要 `0..100` 输入时调用：

```python
hand.speed.set_percentage_speeds([50] * 16)
```

`L25Speed` 是同一显式百分比换算的值对象。事件流与 `get_snapshot()` 返回的
`speeds` 都是协议整数；统一快照标注 `unit="protocol_raw"`、`encoding="u8"`。
