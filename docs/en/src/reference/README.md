# linkerbot-hand User Manual

`linkerbot-hand` is a Python SDK for connecting to and controlling LinkerHand dexterous hands. It supports L6, O6, L20Lite, L20, O20, L30, and O30.

> [!TIP]
> For your first session, follow this workflow:
>
> 1. [Quick Start](./quick-start.md): get the source and install the SDK.
> 2. [Connecting Devices](#connecting-devices): [prepare the communication interface](./connection/prepare.md), then choose [manual connection](./connection/manual.md) or [automatic connection](./connection/automatic.md).
> 3. [Device Features](#models): choose your model to open its feature guide.

## Quick Start

[Quick Start](./quick-start.md): get the source, install the SDK, and read device angles.

## Connecting Devices

Linux SocketCAN users should [prepare the communication interface](./connection/prepare.md) before choosing a manual or automatic connection.

- [Manual Connection](./connection/manual.md): choose an example for your adapter and supply its connection parameters.
- [Automatic Connection](./connection/automatic.md): let the SDK identify and connect to one or more devices.

<a id="models"></a>

## Device Features

| Model | Features |
| --- | --- |
| [L6](./tutorials/l6.md) | Device Identity, Device Capabilities, Angle Control, Speed Control, Torque Control, Reading Sensor Data |
| [O6](./tutorials/o6.md) | Device Identity, Device Capabilities, Angle Control, Speed Control, Setting Acceleration, Reading Sensor Data |
| [L20Lite](./tutorials/l20lite.md) | Device Identity, Device Capabilities, Angle Control, Speed Control, Setting Acceleration, Reading Sensor Data |
| [L20](./tutorials/l20.md) | Device Identity, Device Capabilities, Angle Control, Speed Control, Reading Sensor Data |
| [O20](./tutorials/o20.md) | Device Identity, Device Capabilities, Angle Control, Speed Control, Reading Sensor Data |
| [L30](./tutorials/l30.md) | Device Identity, Device Capabilities, Angle Control, Speed Control, Reading Sensor Data, Enabling and Disabling, Device Calibration |
| [O30](./tutorials/o30.md) | Device Identity, Device Capabilities, Angle Control, Speed Control, Setting Acceleration, Reading Sensor Data, Torque Limits, Setting Motion Times |

## Further Information

- [Troubleshooting](./troubleshooting/README.md): resolve installation, connection, reading, and control issues.
- [Updating the SDK](./sdk-update.md): update your SDK installation.
