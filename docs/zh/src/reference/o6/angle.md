# 角度控制

O6 的默认角度接口收发 6 个协议 `u8` 值，范围均为 `0..255`。顺序为
`thumb_flex`、`thumb_abd`、`index`、`middle`、`ring`、`pinky`。

## 协议值控制

```python
from linkerbot.hand import O6

with O6(side="left", interface_name="can0") as hand:
    hand.angle.set_angles([128, 96, 160, 160, 160, 160])
```

`set_angles()` 不做方向或单位换算，只接受 6 个 `0..255` 的 Python `int`。

## 显式百分比控制

```python
from linkerbot.hand.o6 import O6Angle

hand.angle.set_percentage_angles([50, 30, 60, 60, 60, 60])
hand.angle.set_percentage_angles(O6Angle(50, 30, 60, 60, 60, 60))
```

该 helper 把每轴 `0..100` 线性映射到协议 `0..255`。默认反馈不执行逆换算。

## 读取与缓存

```python
data = hand.angle.get_blocking(timeout_ms=500)
print(data.angles)  # tuple[int, ...]，协议值
print(data.timestamp)

cached = hand.angle.get_snapshot()
```

流式事件中的 `event.data.angles` 使用相同协议值。统一接口及数据集边界见
[协议值、百分比与显式换算](../angle-coordinates.md)。
