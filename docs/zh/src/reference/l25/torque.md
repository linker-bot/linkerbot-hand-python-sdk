# 力矩/电流限制控制与读取

L25 的扭矩字段按协议关节顺序收发 16 个 `0..255` 整数。默认路径不执行百分比或
物理单位换算。

```python
from linkerbot.hand import L25

with L25(side="left", interface_name="can0") as hand:
    hand.torque.set_torques([128] * 16)

    data = hand.torque.get_blocking(timeout_ms=500)
    print(data.torques)  # tuple[int, ...]
```

需要 `0..100` 相对输入时调用：

```python
hand.torque.set_percentage_torques([50] * 16)
```

`L25Torque` 是同一显式百分比换算的值对象。事件流与 `get_snapshot()` 返回的
`torques` 都是协议整数；统一快照标注 `unit="protocol_raw"`、`encoding="u8"`。
