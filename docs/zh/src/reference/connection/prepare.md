# 准备通信接口

Linux 的 CAN / CAN FD 设备统一使用 **SocketCAN** 接口；请先安装适配器对应的驱动或固件，确认系统提供 `can0` 等接口，再按型号配置。仅出现串口名不代表已经具备 SocketCAN CAN FD 接口。Windows 的 PCAN、SLCAN，以及两平台的 MiniCANFD 使用独立后端，无需执行以下命令。

## 经典 CAN

L6、O6、L20Lite 和 L20 使用以下配置。`bitrate 1000000` 表示通信速率为 1 Mbit/s。使用时将 `can0` 替换为实际接口名称。

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up
```

## CAN FD

O20、L30 和 O30 使用以下配置。`bitrate 1000000` 表示仲裁速率为 1 Mbit/s，`dbitrate 5000000` 表示数据速率为 5 Mbit/s。使用时将 `can0` 替换为实际接口名称。

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000 dbitrate 5000000 fd on
sudo ip link set can0 up
```

<a id="checks"></a>

接口启用后，请选择[手动连接](./manual.md)或[自动连接](./automatic.md)。

[返回总览](../README.md)
