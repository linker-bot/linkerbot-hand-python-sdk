# 角度控制

L20Lite 的默认角度接口收发 10 个协议 `u8` 值，范围均为 `0..255`。关节顺序见
[型号说明](./README.md#关节说明)。SDK 会按协议拆分和重组两帧，公开顺序保持不变。

## 协议值控制

```python
from linkerbot.hand import L20lite

with L20lite(side="left", interface_name="can0") as hand:
    hand.angle.set_angles([128] * 10)
```

`set_angles()` 只接受 10 个 `0..255` 的 Python `int`，不执行百分比换算。

## 显式百分比控制

```python
from linkerbot.hand.l20lite import L20liteAngle

hand.angle.set_percentage_angles([50] * 10)
hand.angle.set_percentage_angles(L20liteAngle.from_list([50.0] * 10))
```

该 helper 把每轴 `0..100` 线性映射到协议 `0..255`。

## 读取与缓存

```python
data = hand.angle.get_blocking(timeout_ms=500)
print(data.angles)  # tuple[int, ...]，10 个协议值

cached = hand.angle.get_snapshot()
```

阻塞读取会等待两帧组成同一完整样本；流式事件和缓存使用相同协议元组。统一接口说明见
[协议值、百分比与显式换算](../angle-coordinates.md)。
