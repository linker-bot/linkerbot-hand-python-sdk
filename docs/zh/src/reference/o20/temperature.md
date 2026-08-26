# 温度读取

通过 `hand.temperature` 读取 O20 的 16 个电机温度寄存器值。只读。

- **数量**:16 个电机
- **单位**:协议资料未给出，当前按原始 `count` 发布
- **数据类型**:`uint8`,一个电机 1 字节

统一 `SensorSample` 标注 `unit="count"`、`encoding="u8"`、
`physical_unit=None`、`scale=None`、`offset=None`。寄存器名称表示温度，但在固件资料确认
单位和比例前，不能把返回字节直接标成 °C。

## 阻塞读取

```python
from linkerbot.hand.o20 import O20
from linkerbot.exceptions import TimeoutError

with O20(side="right") as hand:
    try:
        data = hand.temperature.get_blocking(timeout_ms=1000)
        print("温度寄存器值:", data.temperatures)  # tuple[int, ...], 16 个原始计数
        print("时间戳： ", data.timestamp)
    except TimeoutError:
        print("温度读取超时")
```

## 缓存读取

```python
from linkerbot.hand.o20 import O20

with O20(side="right") as hand:
    hand.temperature.get_blocking(timeout_ms=1000)
    data = hand.temperature.get_snapshot()
    if data is not None:
        print(data.temperatures)
```

## 阈值监控

只有在目标固件的温度标度经过确认后，才能把业务温度阈值换算为协议 count。以下示例中的
阈值必须由调用方根据该固件的已验证标度提供，不能直接解释为摄氏度：

```python
from linkerbot.hand.o20 import O20

RAW_THRESHOLD = 60  # 示例 count；必须替换为目标固件的已验证阈值

with O20(side="right") as hand:
    data = hand.temperature.get_blocking(timeout_ms=1000)
    hot = [
        (motor, temp)
        for motor, temp in enumerate(data.temperatures)
        if temp >= RAW_THRESHOLD
    ]
    if hot:
        print("超温电机：")
        for motor_index, temp in hot:
            print(f"  motor {motor_index + 1}: raw={temp}")
```
