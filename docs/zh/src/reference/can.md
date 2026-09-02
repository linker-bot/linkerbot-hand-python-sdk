# 经典 CAN transport

L6、O6、L20Lite 和 L20 使用经典 CAN，当前 profile 要求 1 Mbit/s。设备协议和生命周期
由 `linkerbot-hand` 管理，物理连接、收发、路由和发送回执由
`linkerbot-transport` 管理。

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

## 同一总线多个设备

同一进程、同一物理总线上的设备应复用同一个 transport 描述：

```python
transport = CANTransport("can0")

left = HandDevice(model="l6", side="left", transport=transport)
right = HandDevice(model="o6", side="right", transport=transport)

left.open()
right.open()
try:
    ...
finally:
    right.close()
    left.close()
```

`linkerbot-transport` 对同一物理 key 只建立一个共享 runtime、一个 RX owner 和一个 TX
arbiter。每个 `HandDevice` 仍有独立协议 route、命令调度、轮询计划、缓存和连接代次。

同一接口配置不一致时应失败，不能让“先打开者”的参数静默覆盖后续设备。不同进程无法共享
内存仲裁器；同一总线的多个 ROS 节点应运行在同一进程或 component container。

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
