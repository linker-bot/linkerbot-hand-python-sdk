# 力矩控制

通过 `hand.torque` 设置 O20 的 16 个协议电流上限目标。协议整数范围为 `0..1000`；
默认接口不换算为 mA。协议标注的比例约为 6.5 mA/LSB。

- **数量**:16 个电机，按 motor ID 1~16 顺序
- **范围**:0 ~ 1000(`O20_TORQUE_MIN` / `O20_TORQUE_MAX`)
- **控制模式**:位置模式下每次连接后设置一次即可保持
- **只写**:力矩没有 `get_blocking`(硬件不回传实际力矩，读电流用 [`hand.current`](./current.md))

## 设置力矩

### 所有电机统一力矩

```python
from linkerbot.hand.o20 import O20

with O20(side="right") as hand:
    hand.torque.set_all(400)  # 所有 16 个电机力矩上限 = 400
```

### 每个电机独立力矩

```python
from linkerbot.hand.o20 import O20

with O20(side="right") as hand:
    torques = [
        400,
        300,
        400,
        300,  # 拇指
        200,
        400,
        400,  # 食指
        200,
        400,
        400,  # 中指
        200,
        400,
        400,  # 无名指
        200,
        400,
        400,
    ]  # 小指
    hand.torque.set_torques(torques)
```

## 读取上次下发的力矩目标 (缓存)

`hand.torque` 没有阻塞读——设备不主动回复实际力矩。`get_snapshot()` 返回**上次 `set_torques` / `set_all` 缓存的下发值**,可用于确认当前限位：

```python
from linkerbot.hand.o20 import O20

with O20(side="right") as hand:
    hand.torque.set_all(400)
    data = hand.torque.get_snapshot()
    if data is not None:
        print("已下发力矩：", data.torques)  # tuple[int, ...]
        print("时间戳： ", data.timestamp)
```

想知道**实际电机电流**(单位 mA) 用 [`hand.current`](./current.md):

```python
current = hand.current.get_blocking(timeout_ms=1000)
print("实测电流 (mA):", current.currents)
```

## 完整示例

```python
from linkerbot.hand.o20 import O20

with O20(side="right") as hand:
    hand.speed.set_all(40)
    hand.torque.set_all(400)  # 力矩上限
    hand.angle.set_angles([60, 75, 90, 65, 0, 90, 90, 0, 90, 90, 0, 90, 90, 0, 90, 90])

    print("已下发力矩：", hand.torque.get_snapshot().torques)
    print("实测电流： ", hand.current.get_blocking(timeout_ms=1000).currents)
```

需要 mA 时显式使用 `protocol_current_limits_to_milliamps()` 或
`milliamps_to_protocol_current_limits()`。`get_snapshot()` 仍只表示目标缓存，不是反馈。
capability 中该命令范围的 `unit="count"`、`physical_unit="mA"`、`scale=6.5`、
`offset=0`。

## 参数校验

`set_torques` 与 `set_all` 都会校验参数：

| 校验项 | 触发条件                      | 抛出              |
| ------ | ----------------------------- | ----------------- |
| 长度   | `set_torques` 传入元素数 ≠ 16 | `ValidationError` |
| 类型   | 任一值不是 int                | `ValidationError` |
| 范围   | 值 < 0 或 > 1000              | `ValidationError` |
