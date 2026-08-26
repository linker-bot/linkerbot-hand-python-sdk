# 速度控制与读取

L25 的速度接口按协议关节顺序收发 16 个 `0..255` 整数。默认路径不执行百分比换算。

```python
from linkerbot.hand import L25

with L25(side="left", interface_name="can0") as hand:
    hand.speed.set_speeds([128] * 16)

    data = hand.speed.get_blocking(timeout_ms=500)
    print(data.speeds)  # tuple[int, ...]
```

需要 `0..100` 输入时调用：

```python
hand.speed.set_percentage_speeds([50] * 16)
```

`L25Speed` 是同一显式百分比换算的值对象。事件流与 `get_snapshot()` 返回的
`speeds` 都是协议整数；统一快照标注 `unit="count"`、`encoding="u8"`。协议的物理端点
按关节类型不同：

| 关节 | 文档物理端点 |
| ---- | ------------ |
| `thumb_abd`、`thumb_yaw` | 0..180 rpm |
| `thumb_root1`、`thumb_tip` | 0..30000 rpm |
| 四指 `*_abd`、`*_tip` | 0.02..40 mm/s |
| 四指 `*_root1` | 未给出 |

资料只给 raw `0..255` 和上述端点，没有确认线性关系。物理范围保存在逐关节
`speed_range`；由于通道混合 rpm、mm/s 和未知单位，传感器级 `physical_unit=None`，所有
逐关节 `scale/offset` 也保持 `None`。
