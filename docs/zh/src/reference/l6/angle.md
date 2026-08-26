# 角度控制

L6 的默认角度接口收发 6 个协议 `u8` 值，范围均为 `0..255`。顺序为
`thumb_flex`、`thumb_abd`、`index`、`middle`、`ring`、`pinky`。

## 协议值控制

```python
from linkerbot.hand import L6

with L6(side="left", interface_name="can0") as hand:
    hand.angle.set_angles([128, 96, 160, 160, 160, 160])
```

`set_angles()` 要求恰好 6 个 Python `int`，不会进行百分比或物理角度换算。超出
`0..255`、传入浮点数或数量错误时抛出 `ValidationError`。

## 显式百分比控制

```python
from linkerbot.hand.l6 import L6Angle

hand.angle.set_percentage_angles([50, 30, 60, 60, 60, 60])
hand.angle.set_percentage_angles(L6Angle(50, 30, 60, 60, 60, 60))
```

该 helper 把每轴 `0..100` 线性映射到协议 `0..255`。默认读取仍返回协议值；需要
百分比反馈时，由调用方显式使用统一接口的 `raw_to_percent()` 或
`L6Angle.from_raw()`。

## 读取与缓存

```python
data = hand.angle.get_blocking(timeout_ms=500)
print(data.angles)  # tuple[int, ...]，协议值
print(data.timestamp)

cached = hand.angle.get_snapshot()  # 不产生新查询
```

流式事件中的 `event.data.angles` 也是相同的协议元组。完整的统一接口数据契约见
[协议值、百分比与显式换算](../angle-coordinates.md)。
