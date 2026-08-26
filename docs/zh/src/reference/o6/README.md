# O6 灵巧手

## 快速开始

```python
from linkerbot.hand import O6

with O6(side="left", interface_name="can0") as hand:
    # 直接发送 6 个协议 u8 位置值
    hand.angle.set_angles([128, 96, 160, 160, 160, 160])

    # 读取角度
    data = hand.angle.get_blocking(timeout_ms=500)
    print(data.angles)
```

**构造参数**

| 参数             | 类型                  | 说明                                                                          |
| ---------------- | --------------------- | ----------------------------------------------------------------------------- |
| `side`           | `"left"` \| `"right"` | 左手或右手                                                                    |
| `interface_name` | `str`                 | CAN 接口名，如 `"can0"`                                                       |
| `interface_type` | `str`                 | CAN 接口类型，默认 `"socketcan"`。Windows 用法参考 [CAN 总线](../can.md) 文档 |

角度、速度、加速度和力矩 manager 的默认控制与读回均使用协议整数；需要 `0..100`
输入时调用对应的 `set_percentage_*()`。RPM、mA 和度/秒²只通过显式 helper 换算。
本版本不再提供用户 LUT 或角度映射路径。

## 关节说明

| 索引 | 名称     | 标识         |
| ---- | -------- | ------------ |
| 0    | 拇指弯曲 | `thumb_flex` |
| 1    | 拇指侧摆 | `thumb_abd`  |
| 2    | 食指     | `index`      |
| 3    | 中指     | `middle`     |
| 4    | 无名指   | `ring`       |
| 5    | 小指     | `pinky`      |

## 功能模块

| 模块                | 说明                            |
| ------------------- | ------------------------------- |
| `hand.angle`        | 关节角度控制与读取              |
| `hand.speed`        | 协议速度控制与读取；RPM 显式换算 |
| `hand.acceleration` | 加速度控制                      |
| `hand.torque`       | 协议电流限制控制与读取；mA 显式换算 |
| `hand.temperature`  | 温度读取                        |
| `hand.force_sensor` | 力传感器数据                    |
| `hand.fault`        | 故障读取                        |
| `hand.version`      | 设备版本信息                    |

## 统一流式读取

O6 提供统一的事件流接口，通过 `hand.stream()` 和 `hand.start_polling()` 获取传感器数据。
构造器默认不启动轮询；调用方必须显式选择数据源和周期，并在不再需要时停止轮询。

```python
from linkerbot.hand import O6
from linkerbot.hand.o6 import SensorSource, AngleEvent, TemperatureEvent

with O6(side="left", interface_name="can0") as hand:
    # 可同时指定多个传感器及各自的轮询间隔（秒）
    # 再次调用 start_polling() 会替换之前的设置
    hand.start_polling({SensorSource.ANGLE: 0.05, SensorSource.TEMPERATURE: 1.0})

    for event in hand.stream():
        match event:
            case AngleEvent(data=ad):
                print(f"协议角度：{ad.angles}")
            case TemperatureEvent(data=td):
                print(f"温度：{td.temperatures.to_list()}")

    hand.stop_polling()
    hand.stop_stream()
```

**省略 `intervals` 时的 `start_polling()` 计划**

| 值                          | 说明     | 显式启动后的默认频率 |
| --------------------------- | -------- | -------------------- |
| `SensorSource.ANGLE`        | 角度     | 60 Hz                |
| `SensorSource.TORQUE`       | 扭矩     | 不默认轮询           |
| `SensorSource.SPEED`        | 速度     | 不默认轮询           |
| `SensorSource.ACCELERATION` | 加速度   | 不默认轮询           |
| `SensorSource.TEMPERATURE`  | 温度     | 不默认轮询           |
| `SensorSource.FAULT`        | 故障     | 不默认轮询           |
| `SensorSource.FORCE_SENSOR` | 力传感器 | 30 Hz                |

## 快照

获取所有传感器最新缓存数据：

```python
snap = hand.get_snapshot()
print(snap.angle)  # AngleData | None
print(snap.temperature)  # TemperatureData | None
```
