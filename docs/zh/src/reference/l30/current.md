# 电流读取

通过 `hand.current` 读取 L30 的 17 个关节电流协议原值。旧兼容入口返回整数原值，
每单位为 6.5 mA；统一 `HandDevice` 快照的 `current.values` 返回 mA，并在
`current.raw_values` 保留同次采集的协议原值。

## 阻塞读取

```python
from linkerbot.hand import L30

with L30() as hand:
    data = hand.current.get_blocking(timeout_ms=1000)
    print(data.currents)  # 协议原值；乘 6.5 得到 mA
```

## 缓存读取

```python
from linkerbot.hand import L30

with L30() as hand:
    hand.current.get_blocking(timeout_ms=1000)
    data = hand.current.get_snapshot()
    if data is not None:
        print(data.currents)  # 协议原值
```

## 完整示例

```python
from linkerbot.hand import L30

with L30() as hand:
    current = hand.current.get_blocking(timeout_ms=1000)
    print("电流协议原值：", current.currents)
```
