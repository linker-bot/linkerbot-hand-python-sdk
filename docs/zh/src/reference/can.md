# 经典 CAN transport

L6、O6、L20Lite 和 L20 使用经典 CAN，当前 profile 要求 1 Mbit/s。设备协议和生命周期
由 `linkerbot-hand` 管理，物理连接、收发、路由和发送回执由
`linkerbot-transport` 管理。

当前维护范围为一通道一只手；多只手使用不同 CAN 接口。

## Linux SocketCAN 配置

安装工具：

```bash
sudo apt update
sudo apt install -y can-utils iproute2
```

配置接口：

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up
ip -details -statistics link show can0
```

SDK 默认不替应用修改系统网络接口。先完成 `ip link` 配置，再调用 `open()`。

## 构造

```python
from linkerbot_hand import HandDevice
from linkerbot_transport.can import CANTransport

transport = CANTransport(
    interface_name="can0",
    interface_type="socketcan",
)
hand = HandDevice(
    model="l6",
    side="left",
    transport=transport,
)
```

`CANTransport` 构造同样是零 I/O。`hand.open()` 才获取 transport lease、打开连接并读取
设备身份。

有左右手的型号必须传 `left` 或 `right`。profile 根据 side 选择协议 endpoint；应用不应
自行硬编码 CAN ID。

## 其他 python-can backend

`interface_type` 传给 classic CAN backend，可按 python-can 支持情况使用 `pcan` 等
接口：

```python
transport = CANTransport(
    interface_name="PCAN_USBBUS1",
    interface_type="pcan",
)
```

具体系统驱动、通道命名和权限由对应 python-can backend 决定。

## 监控和排查

被动查看总线：

```bash
candump -tz -x can0
```

查看系统错误状态：

```bash
ip -details -statistics link show can0
```

设备无响应时依次确认：

- 灵巧手和 CAN 适配器供电；
- CAN_H、CAN_L 和 GND；
- 总线两端 120 Ω 终端电阻；
- 接口为 UP 且 bitrate 为 1 Mbit/s；
- 构造型号和 side 与设备一致；
- 是否有另一个进程绕过共享 runtime 同时发送；
- `candump` 是否看到请求、回复、error frame 或 bus-off；
- `hand.get_health()` 中的 transport error、route miss、timeout 和队列深度。

`close()` 只释放 SDK 的 transport lease，不执行 `ip link set can0 down`。系统接口可能
仍被其他设备或诊断工具使用。
