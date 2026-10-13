# 自动连接

<a id="automatic"></a>

SDK 支持自动识别一个或多个设备，并根据识别结果创建连接对象。

## 连接设备

请根据设备数量选择连接示例。使用 Linux SocketCAN 时，请先[准备通信接口](./prepare.md)。

`discover_hands()` 默认在 Linux 扫描 SocketCAN 和 MiniCANFD，在 Windows 扫描 PCAN、SLCAN 和 MiniCANFD。Linux 的 CAN / CAN FD 接口统一按 SocketCAN 使用，接口需先由驱动或固件提供并启用。


### 自动发现一个设备

```python
from linkerbot_hand import discover_hands
# 扫描并识别设备
report = discover_hands()
# require_one() 要求恰好一台设备可供连接，请只接入一台
hand = report.require_one().create_hand_device()
try:
    # 打开设备连接
    hand.open()
    # 读取当前原始角度
    print("当前角度：", hand.get_angles(timeout=1.0).raw)
finally:
    # 关闭连接，释放资源
    hand.close()
```

### 自动发现多个设备

```python
from linkerbot_hand import discover_hands
# 扫描并识别设备
report = discover_hands()
# 未找到设备时停止执行
if not report.verified_candidates:
    raise RuntimeError("未找到设备")
# 逐台连接设备
for candidate in report.verified_candidates:
    # 根据识别结果创建设备对象
    hand = candidate.create_hand_device()
    try:
        # 打开设备连接
        hand.open()
        print("设备型号：", candidate.model)
        # 读取当前原始角度
        print("当前角度：", hand.get_angles(timeout=1.0).raw)
    finally:
        # 关闭连接，释放资源
        hand.close()
```

未找到设备、只获得部分身份或发生连接清理错误时，请查看 `report.failures`，再按[故障排查](../troubleshooting/README.md)处理。`require_one()` 在没有设备、存在多个候选或身份不完整时会报错。

## 适配器参数说明

仅扫描某类适配器时，将示例中的 `report = discover_hands()` 替换为对应调用。

### PCAN（Windows）

安装厂商驱动和 PCANBasic 库后，SDK 自动枚举可用 PCAN 通道，无需填写通道数量。

```python
from linkerbot_hand import DiscoveryConfig, discover_hands
# 扫描可用 PCAN 通道上的经典 CAN 手
report = discover_hands(DiscoveryConfig(backends=("pcan",)))
```

### SLCAN（Windows）

<a id="slcan"></a>

SDK 自动枚举 USB VID/PID 为 `16D0:117E` 的 CANable 2.0 串口，无需填写串口名或设备数量。
串口速率默认为 `115200`。默认配置可直接使用：

```python
from linkerbot_hand import DiscoveryConfig, discover_hands
# 自动枚举符合 USB 标识的串口
report = discover_hands(DiscoveryConfig(backends=("slcan",)))
```

其他 USB 标识的适配器或需要限定串口时，再指定 `channels`，多个串口可写成 `("COM5", "COM6")`。仅在适配器要求不同串口速率时，通过 `bus_options` 设置 `tty_baudrate`：

```python
from linkerbot_hand import DiscoveryConfig, PythonCANDiscoveryConfig, discover_hands
# 扫描并识别设备
report = discover_hands(
    DiscoveryConfig(
        backends=("slcan",),
        slcan=PythonCANDiscoveryConfig(
            # 填写实际串口名
            channels=("COM5",),  # 多个适配器示例：("COM5", "COM6")
        ),
    )
)
```

### MiniCANFD

<a id="minicanfd"></a>

安装厂商驱动和动态库后，SDK 自动识别适配器数量，无需填写设备编号或 `linux_channels`。Linux 使用 `libcanbus.so`，Windows 使用 `HCanbus.dll`；动态库需与 Python 位数一致，并能由系统找到。

Linux 对每个适配器先检查通道 `0`；未获得身份候选且连接已成功关闭时，再检查同一适配器的通道 `1`。通道 `0` 不是 FD 通道时，直接检查 `1`；在 `0` 找到候选后不再检查 `1`。两路检查完成且均无候选时，报告中返回 `no_hand_found`。

Windows 按厂商库枚举的可用 FD 通道扫描。只扫描 MiniCANFD 时使用：

```python
from linkerbot_hand import DiscoveryConfig, discover_hands
# 扫描并识别设备
report = discover_hands(
    DiscoveryConfig(
        backends=("minicanfd",),
    )
)
```

找不到动态库时，可在 `DiscoveryConfig` 中通过 `MiniCANFDDiscoveryConfig(library_path=...)` 指定实际路径：

```python
from linkerbot_hand import DiscoveryConfig, MiniCANFDDiscoveryConfig, discover_hands
# Linux 路径示例；Windows 可填写 r"C:\MiniCANFD\HCanbus.dll"
report = discover_hands(
    DiscoveryConfig(
        backends=("minicanfd",),
        minicanfd=MiniCANFDDiscoveryConfig(library_path="/opt/minicanfd/libcanbus.so"),
    )
)
```

手动指定 `linux_channels` 或全局 `DiscoveryConfig.channels` 后，只检查指定通道，不再自动从 `0` 回退到 `1`。手动构造连接的通道编号见[手动连接](./manual.md#minicanfd)。

连接成功后，请在 `hand.open()` 与 `hand.close()` 之间调用[对应型号的功能](../README.md#models)。使用结束后，调用 `hand.close()` 释放连接。

[返回总览](../README.md)
