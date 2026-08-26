# 电流上限（协议称“力矩”）

L30 的“力矩”命令实际设置 17 个关节的协议电流上限，范围为 `60..800`。型号 manager
的 `set_torques()`、`set_all()` 和统一 `HandDevice.submit_torques()` 都直接接受协议整数，
不再隐式接受 mA。

```python
from linkerbot.hand import L30

with L30() as hand:
    hand.torque.set_torques([100] * 17)
    hand.torque.set_all(120)

    cached = hand.torque.get_snapshot()
    if cached is not None:
        print(cached.torques)  # tuple[int, ...]，最近协议目标
```

L30 不提供该字段的实测反馈。`get_snapshot()` 只是最近一次成功编码并发送的目标缓存，
不能解释为实测力矩或实测电流；实测电流应读取 [`hand.current`](./current.md)。

协议文档给出的比例为每单位 6.5 mA。需要按 mA 构造或展示限制时显式换算：

```python
from linkerbot.hand.l30 import (
    milliamps_to_protocol_current_limits,
    protocol_current_limits_to_milliamps,
)

protocol_target = milliamps_to_protocol_current_limits([650.0] * 17)
hand.torque.set_torques(list(protocol_target))
print(protocol_current_limits_to_milliamps(list(protocol_target)))
```

capability 将命令单位标为 `count`，范围 `60..800`，并声明 `physical_unit="mA"`、
`scale=6.5`、`offset=0`。
