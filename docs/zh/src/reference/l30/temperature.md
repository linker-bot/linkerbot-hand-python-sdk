# 温度读取

通过 `hand.temperature` 读取 L30 的 17 个关节温度数据。

协议每关节使用一个 `u8`，每计数为 1°C。统一 `SensorSample` 因此标注
`unit="degree_celsius"`、`encoding="u8"`、`physical_unit="degree_celsius"`、
`scale=1`、`offset=0`；返回值保持协议整数。

## 阻塞读取

```python
from linkerbot.hand import L30

with L30() as hand:
    data = hand.temperature.get_blocking(timeout_ms=1000)
    print(data.temperatures)
```

## 缓存读取

```python
from linkerbot.hand import L30

with L30() as hand:
    hand.temperature.get_blocking(timeout_ms=1000)
    data = hand.temperature.get_snapshot()
    if data is not None:
        print(data.temperatures)
```

## 完整示例

```python
from linkerbot.hand import L30

with L30() as hand:
    temperature = hand.temperature.get_blocking(timeout_ms=1000)
    print("温度：", temperature.temperatures)
```
