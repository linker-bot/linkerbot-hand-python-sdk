# 电流限制控制（协议称“扭矩”）

L6 的扭矩字段在线上是 6 个 `0..255` 协议字节，用作电机电流限制。默认控制和反馈
保留这些协议整数，不隐式换算为百分比或 mA。

## 协议值控制

```python
from linkerbot.hand import L6

with L6(side="left", interface_name="can0") as hand:
    hand.torque.set_torques([128, 96, 160, 160, 160, 160])
```

需要 `0..100` 相对输入时调用显式方法：

```python
hand.torque.set_percentage_torques([50, 30, 60, 60, 60, 60])
```

## 读取协议值

```python
data = hand.torque.get_blocking(timeout_ms=500)
print(data.torques)  # tuple[int, ...]

cached = hand.torque.get_snapshot()
if cached is not None:
    print(cached.torques)
```

事件流中的 `TorqueEvent.data.torques` 也是同一组协议整数。

## 显式 mA 换算

协议文档给出的满量程约为 1400 mA，即 `protocol * 1400 / 255`。转换不会自动进入
控制或反馈路径：

```python
from linkerbot.hand.l6 import L6Torque

milliamps = L6Torque.from_raw(list(data.torques)).to_milliamps()
protocol_target = L6Torque.from_milliamps([700.0] * 6).to_raw()
hand.torque.set_torques(protocol_target)
```

统一 capability 将命令范围和反馈标记为 `protocol_raw` / `u8`。
