# 手动连接

请根据适配器选择连接示例，填写实际通道、型号和左右手，并调用 `hand.open()` 连接设备。

<a id="socketcan"></a>

## SocketCAN（CAN / CAN FD）

请先[准备通信接口](./prepare.md)，连接示例中的 `model` 和 `side` 分别指定型号和左右手，使用时请将示例中 `can0` 、 `model` 和 `side` 替换为实际接口名称。

### 经典 CAN

本示例适用于O6、L6、L20Lite 和 L20。

```python
from linkerbot_hand import HandDevice
from linkerbot_transport.can import CANTransport
# 创建设备对象
hand = HandDevice(
    model="O6",  # 设备型号
    side="right",  # 左右手：left 或 right；L30 无需填写
    transport=CANTransport("can0"),
)
try:
    # 打开设备连接
    hand.open()
    # 读取当前原始角度
    print(hand.get_angles(timeout=1.0).raw)
finally:
    # 关闭连接，释放资源
    hand.close()
```

### CAN FD

本示例适用于O30、O20 和 L30。

```python
from linkerbot_hand import HandDevice
from linkerbot_transport.canfd import SocketCANFDTransport
# 创建设备对象
hand = HandDevice(
    model="O30",  # 设备型号
    side="right",  # 左右手：left 或 right；L30 无需填写
    transport=SocketCANFDTransport("can0"),
)
try:
    # 打开设备连接
    hand.open()
    # 读取当前原始角度
    print(hand.get_angles(timeout=1.0).raw)
finally:
    # 关闭连接，释放资源
    hand.close()
```

<a id="pcan-slcan"></a>

## PCAN 与 SLCAN（Windows）

### PCAN 经典 CAN

本示例适用于 Windows 下的 linkerhand 经典 can 系列型号。请先安装厂商驱动和 PCANBasic 库，并确保库与 Python 的位数一致。实际使用时，将 `PCAN_USBBUS1` 替换为实际 PCAN 通道名。Linux 下由驱动提供 `can0` 等接口后，使用上面的 SocketCAN 示例。

```python
from linkerbot_hand import HandDevice
from linkerbot_transport.can import CANTransport
# 创建设备对象
hand = HandDevice(
    model="O6",  # 设备型号
    side="right",  # 左右手：left 或 right；L30 无需填写
    transport=CANTransport("PCAN_USBBUS1", "pcan", bitrate=1_000_000),
)
try:
    # 打开设备连接
    hand.open()
    # 读取当前原始角度
    print(hand.get_angles(timeout=1.0).raw)
finally:
    # 关闭连接，释放资源
    hand.close()
```

### SLCAN CAN FD

本示例适用于 Windows 下的 linkerhand canfd 系列型号，适配器需运行支持 CANable 2.0 CAN FD 扩展的固件。请将 `COM5` 替换为实际串口名。串口速率默认 `115200`，需要其他速率时才通过 `bus_options` 设置 `tty_baudrate`。Linux 按 SocketCAN CAN FD 连接，需确认驱动、固件和接口支持 CAN FD。

```python
from linkerbot_hand import HandDevice
from linkerbot_transport.canfd import PythonCANFDTransport
# 创建设备对象
hand = HandDevice(
    model="O30",  # 设备型号
    side="right",  # 左右手：left 或 right；L30 无需填写
    transport=PythonCANFDTransport(
        "COM5",
        "slcan",
        bitrate=1_000_000,  # 仲裁速率，单位 bit/s
        data_bitrate=5_000_000,  # 数据速率，单位 bit/s
    ),
)
try:
    # 打开设备连接
    hand.open()
    # 读取当前原始角度
    print(hand.get_angles(timeout=1.0).raw)
finally:
    # 关闭连接，释放资源
    hand.close()
```

<a id="minicanfd"></a>

## MiniCANFD

MiniCANFD 支持 Linux 和 Windows。本示例适用于 O20、L30 和 O30。请根据实际设备修改通道编号、型号和左右手。

```python
from linkerbot_hand import HandDevice
from linkerbot_transport.canfd import CANFDConfigOptions, CANFDTransport
# 创建设备对象
hand = HandDevice(
    model="O30",  # 设备型号
    side="right",  # 左右手：left 或 right；L30 无需填写
    transport=CANFDTransport(
        # Windows 为通道编号；Linux 为设备编号
        device_index=0,
        # Windows 固定为 0；Linux 为通道编号
        channel_index=0,
        # 设置仲裁和数据速率，单位 bit/s
        config=CANFDConfigOptions(nom_baud=1_000_000, dat_baud=5_000_000),
    ),
)
try:
    # 打开设备连接
    hand.open()
    # 读取当前原始角度
    print(hand.get_angles(timeout=1.0).raw)
finally:
    # 关闭连接，释放资源
    hand.close()
```

> **系统差异**：Windows 下，`device_index` 表示通道编号，`channel_index` 固定为 `0`。Linux 下，`device_index` 和 `channel_index` 分别表示设备编号和通道编号。

Linux 连接三个适配器且手都在通道 `0` 时，分别使用 `(device_index, channel_index)=(0, 0)`、`(1, 0)`、`(2, 0)`；手接在通道 `1` 时将 `channel_index` 改为 `1`。手动连接不会尝试其他通道；需要自动识别数量和通道回退时，使用[自动连接](./automatic.md#minicanfd)。厂商动态库找不到时，在 `CANFDTransport` 中通过 `library_path` 指定实际路径。

连接成功后，请在 `hand.open()` 与 `hand.close()` 之间调用[对应型号的功能](../README.md#models)。使用结束后，调用 `hand.close()` 释放连接。

将 `hand.open()` 放在 `try` 内，确保打开失败时也会在 `finally` 中调用 `hand.close()`。
清理失败时保留同一个 `HandDevice`，排除驱动或连接故障后重试 `hand.close()`，关闭成功后
再重新打开。清理重试不会重新打开通道，也不会关闭其他设备仍持有租约的共享连接。

[返回总览](../README.md)
