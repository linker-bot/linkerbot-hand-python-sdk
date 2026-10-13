# Preparing the Communication Interface

Use **SocketCAN** for Linux CAN / CAN FD devices. Install the adapter's driver or firmware and confirm that an interface such as `can0` exists, then choose the configuration for your model. A serial port alone does not provide a SocketCAN CAN FD interface. Windows PCAN and SLCAN, and MiniCANFD on both platforms, use separate backends and do not need these commands.

## Classical CAN

Use this configuration for L6, O6, L20Lite, and L20. `bitrate 1000000` sets the bus rate to 1 Mbit/s.

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up
```

## CAN FD

Use this configuration for O20, L30, and O30. `bitrate 1000000` sets the arbitration rate to 1 Mbit/s, `dbitrate 5000000` sets the data rate to 5 Mbit/s, and `fd on` enables CAN FD.

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000 dbitrate 5000000 fd on
sudo ip link set can0 up
```

<a id="checks"></a>

After enabling the interface, choose [manual connection](./manual.md) or [automatic connection](./automatic.md).

[Back to overview](../README.md)
