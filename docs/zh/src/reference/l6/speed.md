# 速度控制

L6 的速度命令使用 6 个 `0..255` 协议整数，顺序为拇指弯曲、拇指侧摆、食指、中指、
无名指、小指。`set_speeds()` 不执行百分比或物理单位换算。

```python
from linkerbot.hand import L6

with L6(side="left", interface_name="can0") as hand:
    hand.speed.set_speeds([128, 128, 192, 192, 192, 192])
```

需要 `0..100` 输入时使用显式方法：

```python
hand.speed.set_percentage_speeds([50, 50, 75, 75, 75, 75])
```

`L6Speed` 是同一百分比换算的值对象，只能传给 `set_percentage_speeds()`，或先调用
`to_raw()` 再传给默认接口。

L6 协议没有当前速度查询命令。`get_snapshot()` 只返回最近一次成功下发的协议目标，
不是实测速度；因此 manager 不提供 `get_blocking()`，统一 capability 也不声明
`SensorType.SPEEDS`。

```python
cached = hand.speed.get_snapshot()
if cached is not None:
    print(cached.speeds)  # tuple[int, ...]，协议目标
```
