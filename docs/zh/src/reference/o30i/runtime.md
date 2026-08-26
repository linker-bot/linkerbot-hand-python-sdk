# 运行时控制与状态

O30i 的常用运行时对象在协议线上都是 36 槽向量，但实际只有 20 个关节。高层 API 的完整读写均使用[实际关节顺序](./implementation-status.md#实际关节定义)，SDK 自动跳过不存在的线上槽位。

## 位置控制

默认协议字节 API：

```python
from linkerbot.hand import O30i

with O30i() as hand:
    hand.angle.set_angles([0x80] * 20, timeout_ms=1000)

    # 只改五指指尖：SDK 关节索引 15～19
    hand.angle.set_slice(15, [0x80] * 5, timeout_ms=1000)
```

这些方法保持设备协议语义。对后 14 个屈伸关节而言，raw `0` 是伸直，raw `255` 是弯曲。
前 6 个方向轴的 raw `0 -> 255` 端点依次是：拇指旋转远离食指 -> 靠近食指、拇指横滚
平行掌心 -> 垂直掌心，以及四指侧摆靠近拇指 -> 远离拇指。

显式百分比 API：

```python
with O30i() as hand:
    hand.angle.set_percentage_angles([50.0] * 20, timeout_ms=1000)
```

型号 manager 和统一 `HandDevice.submit_percentage_angles()` 使用相同的逐轴端点。前 6 个
方向轴正向映射，`0% -> raw 0`、`100% -> raw 255`；后 14 个屈伸轴表示开合，
`0%（弯曲）-> raw 255`、`100%（伸直）-> raw 0`。百分比不是以度为单位的标定角度。

切片末端不能超过 SDK 关节索引 19。以下调用会抛出 `ValidationError`，不会发帧：

```python
hand.angle.set_slice(19, [1, 2])
```

稀疏位置对象 `MI=0x30` 使用与完整向量不同的固件关节编号。`set_sparse()` 的 key 使用 SDK 的 0～19 关节索引，SDK 自动转换。例如索引 15/16 是拇指/食指指尖，线上会变成稀疏关节号 4/9：

```python
hand.angle.set_sparse({15: 0x80, 16: 0x80})
# HOP data: 30 00 04 04 80 09 80
```

每个关节占两个 payload 字节；需要一次设置全部 20 个实际关节时使用 `set_angles()`。

## 位置读取

```python
with O30i() as hand:
    actual = hand.angle.get_blocking(timeout_ms=1000)
    print("实际协议值", actual.angles)

    target = hand.angle.get_target_blocking(timeout_ms=1000)
    print("目标协议值", target)

    # 实测仅确认完整 72 字节读取；按无符号小端返回 20 个实际关节
    raw_u16 = hand.angle.get_u16_blocking(timeout_ms=1000)
```

`get_blocking()` 读取 RTS=0 实际值，并更新 `get_snapshot()` 缓存；`get_target_blocking()` 使用 RTS=1 读取设定值。
主快照角度来自 `MI=0x01`，元数据是 `count/u8`。`get_u16_blocking()` 单独读取实验性的
`MI=0x20` 完整对象，编码为 `u16_le`，不会更新主快照。

## 速度、加速度、转矩和运动时间

这些 manager 接受 20 个 `0..255 count` 协议原始字节：

```python
with O30i() as hand:
    hand.speed.set_all(40)
    hand.acceleration.set_all(20)
    hand.torque.set_all(30)

    hand.speed.set_speeds([40] * 20)
    hand.acceleration.set_accelerations([20] * 20)
    hand.torque.set_torques([30] * 20)

    print(hand.speed.get_blocking(timeout_ms=1000).speeds)
    print(hand.acceleration.get_blocking(timeout_ms=1000).accelerations)
    print(hand.torque.get_blocking(timeout_ms=1000).torques)
```

运动时间每格为 10 ms，可使用 ticks 或严格的毫秒整数：

```python
with O30i() as hand:
    hand.motion_time.set_all_ticks(10)  # 100 ms
    hand.motion_time.set_milliseconds([100] * 20)

    data = hand.motion_time.get_blocking(timeout_ms=1000)
    print(data.ticks)
    print(data.milliseconds)
```

毫秒值必须是 10 的倍数，范围为 0～2550。

## 只读运行状态

```python
with O30i() as hand:
    currents = hand.current.get_blocking(timeout_ms=1000).currents
    voltages = hand.voltage.get_blocking(timeout_ms=1000).voltages
    temperatures = hand.temperature.get_blocking(timeout_ms=1000).temperatures
    faults = hand.fault.get_blocking(timeout_ms=1000)

    if faults.has_fault(0):
        print("槽 0 原始故障字节", faults.faults[0])
```

固件报告没有给出故障字节的稳定 bit 定义，SDK 只保留原始值并以“是否非零”判断该槽是否存在故障。
电流、电压和温度返回值也都是 `count/u8`；协议没有确认 mA、V 或摄氏度比例，默认接口不做
物理换算。统一 capability 可查询这些单位、编码和值域。

## 产品信息、传感器能力与诊断

```python
with O30i() as hand:
    info = hand.version.get_device_info(timeout_ms=1000)
    print(info.product_model, info.protocol_name, info.protocol_version)

    sensor = hand.sensor.get_info(timeout_ms=1000)
    if not sensor.has_sensor_data:
        print("当前设备无可读触觉通道：", sensor.sensor_type)

    errors = hand.diagnostics.get_communication_errors(timeout_ms=1000)
    print(errors.latest_index, errors.history)
```

`get_device_info()` 自动重组 225 字节多帧响应。`sensor.get_info()` 先读取 `MI=0x31` 的 50 字节描述；当前 O30i 实测为 `NO_SENSOR` 且总长度为 0，因此 SDK 不会假定 `0x32～0x34` 数据通道可用。

## 快照、轮询和事件流

```python
from linkerbot.hand import O30i
from linkerbot.hand.o30i import AngleEvent, SensorSource

with O30i() as hand:
    stream = hand.stream(maxsize=100)
    hand.start_polling(
        {
            SensorSource.ANGLE: 1 / 30,
            SensorSource.CURRENT: 1 / 10,
            SensorSource.TEMPERATURE: 1.0,
        }
    )

    for event in stream:
        if isinstance(event, AngleEvent):
            print(event.data.angles)
            break

    snapshot = hand.get_snapshot()
    hand.stop_polling()
    hand.stop_stream()
```

O30i 没有 SDK 管理的设备端周期上报配置，`start_polling()` 是主机主动读。支持的源包括 `ANGLE`、`SPEED`、`ACCELERATION`、`CURRENT`、`VOLTAGE`、`TORQUE`、`TEMPERATURE`、`MOTION_TIME` 和 `FAULT`。
