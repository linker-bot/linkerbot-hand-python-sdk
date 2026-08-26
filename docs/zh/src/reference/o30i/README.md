# O30i CAN FD 灵巧手

O30i 使用 HandProtocol_v1.0（协议名 HOP）。当前固件实测使用 11 位标准 CAN ID：上位机请求 `0x001`，设备响应 `0x401`；报文为 CAN FD、无 BRS（`frame_type=0x04`）。SDK 支持两种后端：

| 后端                 | 参数                              | 平台            | 说明                                |
| -------------------- | --------------------------------- | --------------- | ----------------------------------- |
| 厂商动态库           | `interface_type="ctypes"`（默认） | Linux / Windows | 使用 `libcanbus.so` / `HCanbus.dll` |
| python-can SocketCAN | `interface_type="socketcan"`      | Linux           | 使用系统 `can0` 等 CAN FD 网络接口  |

## 快速开始

厂商动态库后端：

```python
from linkerbot.hand import O30i

with O30i(library_path="/usr/local/lib/libcanbus.so") as hand:
    info = hand.version.get_device_info(timeout_ms=1000)
    print(info.product_model, info.protocol_name, info.protocol_version)

    actual = hand.angle.get_blocking(timeout_ms=1000)
    print(actual.angles)
```

也可以省略 `library_path`，让 SDK 按系统 loader、`LINKERBOT_CANFD_LIB` 环境变量和包内 fallback 顺序查找动态库。

Linux SocketCAN 后端：

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000 dbitrate 5000000 fd on
sudo ip link set can0 up
```

```python
from linkerbot.hand import O30i

with O30i(
    interface_type="socketcan",
    socketcan_channel="can0",
) as hand:
    print(hand.version.get_device_info(timeout_ms=1000))
```

完整的 SocketCAN 配置和排错方式见 [CAN FD 总线](../canfd.md)。

实物诊断默认应只读取版本和当前状态。运动测试必须由应用显式提交目标，并在提交前完成限位、
设备身份和现场安全条件检查；SDK 不会在连接、恢复或关闭时自动点动关节。

## 构造参数

```python
from linkerbot.hand import O30i

hand = O30i(
    request_id=0x001,
    response_id=None,
    device=0,
    channel=0,
    frame_type=0x04,
)
hand.close()
```

| 参数                | 类型                                   | 说明                                       |
| ------------------- | -------------------------------------- | ------------------------------------------ |
| `request_id`        | `int`                                  | 11 位标准帧请求 ID，默认 `0x001`           |
| `response_id`       | `int \| None`                          | 响应 ID；`None` 时为 `request_id \| 0x400` |
| `device`            | `int`                                  | 厂商 CAN FD 适配器索引，默认 0             |
| `channel`           | `int`                                  | 厂商适配器通道索引，默认 0                 |
| `library_path`      | `str \| Path \| None`                  | `libcanbus.so` / `HCanbus.dll` 路径        |
| `config`            | `CANFDConfigOptions \| None`           | 厂商适配器配置                             |
| `frame_type`        | `int \| None`                          | 默认 `0x04`（CAN FD、无 BRS）              |
| `dispatcher`        | `HandProtocolV1DispatcherLike \| None` | 测试、自定义传输或共享 dispatcher          |
| `interface_type`    | `"ctypes" \| "socketcan"`              | 默认 `"ctypes"`                            |
| `socketcan_channel` | `str \| None`                          | SocketCAN 接口名，例如 `"can0"`            |
| `bitrate`           | `int`                                  | SocketCAN 仲裁速率，默认 1 Mbit/s          |
| `data_bitrate`      | `int`                                  | SocketCAN 数据段配置值，默认 5 Mbit/s      |
| `auto_reconfigure`  | `bool`                                 | 是否允许 SDK 配置 SocketCAN 链路           |

推荐始终使用 `with O30i(...) as hand:`，确保收发线程和底层连接被释放。

## 20 个实际关节

固件运行时对象包含 36 个单字节槽位，但 O30i 实际只存在 20 个关节。SDK 读取完整 36 字节后只返回真实关节；完整写入会自动跳过不存在的槽位。

当前只确认协议位置范围为 `0～255`，没有可靠的物理角度标定值。`set_angles()`、
`set_slice()`、`set_sparse()` 和位置读回都使用设备协议字节，不做反转。型号 manager 的
`set_percentage_angles()` 是显式 helper：前 6 个方向轴把 `0%/100%` 映射为 raw
`0/255`，后 14 个屈伸轴按开合约定把 `0%/100%` 映射为 raw `255/0`。统一
`HandDevice` 使用同一套逐轴 capability 端点。

| 索引范围 | 标识                 | 说明                     |
| -------: | -------------------- | ------------------------ |
|        0 | `roll_0`             | 拇指旋转                 |
|     1～5 | `yaw_0`～`yaw_4`     | 拇指横滚及食指到小指侧摆 |
|    6～10 | `root1_0`～`root1_4` | 五指指根 1               |
|   11～14 | `root2_1`～`root2_4` | 食指到小指指根 2         |
|   15～19 | `tip_0`～`tip_4`     | 五指指尖                 |

完整 ID、名称、线上 SI、默认值、已实现和未实现功能见[实现状态与测试范围](./implementation-status.md)。

程序中可通过 `O30I_JOINT_SPECS` 获取同一张表：

```python
from linkerbot.hand.o30i import O30I_JOINT_SPECS

for index, spec in enumerate(O30I_JOINT_SPECS):
    print(index, spec.name, spec.minimum, spec.maximum)
```

## 协议值元数据

默认 API 和统一快照都保留协议解码值，不隐式换算物理量：

| 数据源 | 值域与编码 | 物理元数据 |
| ------ | ---------- | ---------- |
| 8 位位置、速度、加速度、转矩、电流、电压、温度 | `0..255 count` / `u8` | 单位和比例未确认 |
| 故障 | `0..255 dimensionless` / `u8` | `bitmask`；bit 定义未确认，原始字节不丢失 |
| 运动时间 | `0..255 count` / `u8` | `millisecond`，`scale=10`，`offset=0` |
| 16 位位置 `MI=0x20` | `0..65535 count` / `u16_le` | 仅完整 72 字节读取可靠，不进入主快照 |

`u8` 表示无符号 8 位整数，`u16_le` 表示无符号 16 位小端整数。固件的 `MI=0x43`
单位/量程查询目前属于 Experimental：实测常返回 `normalization` 和 `0..255`，不能据此把
上述 count 固定解释成度、RPM、mA、V 或摄氏度。

## 功能模块

| 模块                | 说明                                                    |
| ------------------- | ------------------------------------------------------- |
| `hand.angle`        | 协议位置控制与读取、显式百分比控制、完整 `u16_le` 对象读取 |
| `hand.speed`        | 速度原始字节读写                                        |
| `hand.acceleration` | 加速度原始字节读写                                      |
| `hand.current`      | 电流状态读取                                            |
| `hand.voltage`      | 电压状态读取                                            |
| `hand.torque`       | 转矩原始字节读写                                        |
| `hand.temperature`  | 温度状态读取                                            |
| `hand.motion_time`  | 运动时间读写，每格 10 ms                                |
| `hand.fault`        | 原始故障字节读取                                        |
| `hand.version`      | 225 字节产品与版本信息读取                              |
| `hand.sensor`       | 触觉能力信息读取；先判断 `total_data_length`            |
| `hand.diagnostics`  | `MI=0x4F` 通信错误历史读取                              |
| `hand.protocol`     | 传输无关 HandProtocol_v1.0 原始对象读写                 |

常用操作见 [运行时控制与状态](./runtime.md)，HOP 帧格式及高级访问见 [HandProtocol_v1.0](./hand-protocol-v1.md)。

## 实测安全边界

O30i 当前固件与早期协议文档有若干不一致，SDK 的高层接口有意遵循以下边界：

- `MI=0x00` 关节映射无可靠响应，不提供高层配置接口；
- `MI=0x0C` 使能掩码通信可通但功能不生效，不作为使能状态；
- `MI=0x20` 只提供完整 72 字节读取，不提供局部读写；
- `MI=0x21～0x26` 实测对象长度与旧文档不一致，不做强类型封装；
- `MI=0x35` 配置连续写受限，不封装恢复、保存等危险命令；
- `MI=0x42` 工程服务可能进入 Bootloader、升级或恢复出厂，不提供高层写接口；
- `MI=0x43` 单位/量程查询只作为 Experimental 原始对象访问，不作为 capability 强依赖；
- `MI=0x31` 报告 `total_data_length=0` 时，不读取 `0x32～0x34` 数据通道。

确需访问尚未封装的已确认对象时可使用 `hand.protocol`，调用者需要自行承担固件版本与写入语义风险。
