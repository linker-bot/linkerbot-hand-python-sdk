# 电流上限（协议称“力矩”）

L30 协议把该字段命名为“力矩”，实际设置的是 17 个关节的电流上限。

- **数量**：17 个关节，顺序为 `J1` 到 `J17`
- **协议原值**：60-800，每单位 6.5 mA
- **统一接口**：`HandDevice.submit_torques()` 接受 390-5200 mA；允许浮点值，发送前按 6.5 mA 分辨率取整
- **旧兼容入口**：`hand.torque.set_torques()` / `set_all()` 继续接受 60-800 协议原值
- **注意**：`hand.torque.get_snapshot()` 返回最近一次 SDK 发送的协议目标值，不是硬件实测力矩或电流

## 旧兼容入口设置协议原值

```python
from linkerbot.hand import L30

with L30() as hand:
    hand.torque.set_torques([100] * 17)
```

## 旧兼容入口设置相同协议原值

```python
from linkerbot.hand import L30

with L30() as hand:
    hand.torque.set_all(100)
```

## 读取旧兼容入口的目标缓存

```python
from linkerbot.hand import L30

with L30() as hand:
    hand.torque.set_all(100)
    data = hand.torque.get_snapshot()
    if data is not None:
        print(data.torques)
```

## 统一接口使用 mA

```python
from linkerbot_hand.backends.legacy import create_hand_device

hand = create_hand_device("L30")
hand.open()
try:
    handle = hand.submit_torques([650.0] * 17)
    print(handle.command_id)
finally:
    hand.close()
```
