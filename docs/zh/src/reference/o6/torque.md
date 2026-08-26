# 电流限制控制（协议称“扭矩”）

O6 的扭矩字段使用 6 个 `0..255` 协议字节。默认控制和反馈保留这些整数，不隐式
换算为百分比或 mA。

```python
from linkerbot.hand import O6

with O6(side="left", interface_name="can0") as hand:
    hand.torque.set_torques([128, 96, 160, 160, 160, 160])

    data = hand.torque.get_blocking(timeout_ms=500)
    print(data.torques)  # tuple[int, ...]
```

需要 `0..100` 相对输入时调用：

```python
hand.torque.set_percentage_torques([50, 30, 60, 60, 60, 60])
```

## 显式 mA 换算

O6 文档给出的满量程为 1000 mA，即 `protocol * 1000 / 255`：

```python
from linkerbot.hand.o6 import O6Torque

milliamps = O6Torque.from_raw(list(data.torques)).to_milliamps()
protocol_target = O6Torque.from_milliamps([800.0] * 6).to_raw()
hand.torque.set_torques(protocol_target)
```

事件流中的 `TorqueEvent.data.torques` 也是协议整数。统一快照标注
`unit="protocol_raw"`、`encoding="u8"`。
