# 角度控制

L25 的默认角度接口收发 16 个协议 `u8` 值，范围均为 `0..255`。关节顺序见
[型号说明](./README.md#关节说明)。SDK 会按五指拆分和重组五帧，保留位不会出现在
公开向量中。

## 协议值控制

```python
from linkerbot.hand import L25

with L25(side="left", interface_name="can0") as hand:
    hand.angle.set_angles(list(range(16)))
```

`set_angles()` 只接受 16 个 `0..255` 的 Python `int`，不执行百分比换算。

## 显式百分比控制

```python
from linkerbot.hand.l25 import L25Angle

hand.angle.set_percentage_angles([50] * 16)
hand.angle.set_percentage_angles(L25Angle.from_list([50.0] * 16))
```

该 helper 把每轴 `0..100` 线性映射到协议 `0..255`。

## 读取与缓存

```python
data = hand.angle.get_blocking(timeout_ms=500)
print(data.angles)  # tuple[int, ...]，16 个协议值

cached = hand.angle.get_snapshot()
```

阻塞读取会等待五帧组成同一完整样本；流式事件和缓存使用相同协议元组。统一接口说明见
[协议值、百分比与显式换算](../angle-coordinates.md)。
