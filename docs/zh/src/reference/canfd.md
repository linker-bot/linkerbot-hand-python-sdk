# CAN FD transport

O20、L30 和 O30 使用 CAN FD。当前 capability：

| 型号 | 仲裁速率 | 数据速率 | side |
| --- | ---: | ---: | --- |
| O20 | 1 Mbit/s | 5 Mbit/s | 必填 |
| L30 | 1 Mbit/s | 5 Mbit/s | 不适用，禁止传入 |
| O30 | 1 Mbit/s | 推荐 5 Mbit/s，任意速率均可（BRS off，数据段恒等于标称速率） | 必填 |

协议 profile 负责 CAN ID、标准/扩展帧、FD 和 BRS 标志。应用只选择物理 transport，
不再传旧型号类的 `frame_type`、`node_id`、`host_id` 或 manager 参数。

当前维护范围为一通道一只手；多只手使用不同 CAN FD 接口。

## Linux SocketCAN FD

安装工具：

```bash
sudo apt update
sudo apt install -y can-utils iproute2
```

O20/L30：

```bash
sudo ip link set can0 down
sudo ip link set can0 type can \
  bitrate 1000000 \
  dbitrate 5000000 \
  fd on
sudo ip link set can0 up
```

O30（BRS off，数据段速率不影响通信，因此推荐 5 Mbit/s，但任意速率都可用，示例用 5M）：

```bash
sudo ip link set can0 down
sudo ip link set can0 type can \
  bitrate 1000000 \
  dbitrate 5000000 \
  fd on
sudo ip link set can0 up
```

确认实际配置和错误计数：

```bash
ip -details -statistics link show can0
```

## SocketCANFDTransport

L30：

```python
from linkerbot_hand import HandDevice
from linkerbot_transport.canfd import SocketCANFDTransport

transport = SocketCANFDTransport(
    channel="can0",
    bitrate=1_000_000,
    data_bitrate=5_000_000,
    fd=True,
    auto_reconfigure=False,
)
hand = HandDevice(
    model="l30",
    transport=transport,
)
```

O20：

```python
hand = HandDevice(
    model="o20",
    side="right",
    transport=SocketCANFDTransport(
        "can0",
        bitrate=1_000_000,
        data_bitrate=5_000_000,
    ),
)
```

O30（BRS off，data_bitrate 可任取；示例用 5M，接口配 1M/7M 同样可用）：

```python
hand = HandDevice(
    model="o30",
    side="right",
    transport=SocketCANFDTransport(
        "can0",
        bitrate=1_000_000,
        data_bitrate=5_000_000,
    ),
)
```

transport 和 `HandDevice` 构造都不触碰网络接口。默认
`auto_reconfigure=False`；`open()` 会使用当前系统配置。只有确认当前进程独占接口且具备
`CAP_NET_ADMIN` 时才启用自动配置。

O30 使用 BRS off，数据段恒等于标称速率，接口 dbitrate 不影响 O30 帧的传输速率；
O20/L30 则需让接口 data_bitrate 与各自 capability 对齐。

## 厂商 CAN FD backend

需要厂商动态库时使用 `CANFDTransport`：

```python
from linkerbot_transport.canfd import CANFDTransport

transport = CANFDTransport(
    device_index=0,
    channel_index=0,
    library_path="/path/to/libcanbus.so",
)
```

厂商 backend 参数只属于 transport。`HandDevice` 构造、生命周期、命令和状态 API 与
SocketCAN 完全一致。

## 监控和故障处理

查看 CAN FD 标志和时间戳：

```bash
candump -tz -x can0
```

无响应或频率下降时检查：

- `ip -details -statistics link show can0` 中的 FD、bitrate、dbitrate 和 bus-off；
- 设备是否发送 BRS，以及适配器是否支持对应数据速率；
- 型号、side 和 firmware profile 是否匹配；
- RX route miss、missing fragment、transaction timeout 和 TX queue latency；
- 完整触觉事务产生的帧数和设备首帧延迟；
- 是否存在绕过 `linkerbot-transport` 的第二个周期查询方。

`close()` 释放 SDK lease，但不会关闭系统的 `can0` 网络接口。
