# linkerbot-hand 使用手册

`linkerbot-hand` 是用于连接和控制 LinkerHand 系列灵巧手的 Python SDK。支持型号有：L6、O6、L20Lite、L20、O20、L30 和 O30。

> [!TIP]
> 首次使用时，请完整阅读使用手册

## 快速开始

[快速开始](./quick-start.md)：获取源码、安装 SDK，并快速读取设备角度。

## 连接设备

请根据适配器型号[准备通信接口](./connection/prepare.md)，再选择手动或自动连接：

- [手动连接](./connection/manual.md)：根据适配器选择示例，填写连接参数。
- [自动连接](./connection/automatic.md)：由 SDK 识别设备，连接单台或多台设备。

<a id="models"></a>

## 设备功能

| 型号 | 功能 |
| --- | --- |
| [L6](./tutorials/l6.md) | 身份识别、能力查询、角度控制、速度控制、力矩控制、传感器数据读取 |
| [O6](./tutorials/o6.md) | 身份识别、能力查询、角度控制、速度控制、加速度设置、传感器数据读取 |
| [L20Lite](./tutorials/l20lite.md) | 身份识别、能力查询、角度控制、速度控制、加速度设置、传感器数据读取 |
| [L20](./tutorials/l20.md) | 身份识别、能力查询、角度控制、速度控制、传感器数据读取 |
| [O20](./tutorials/o20.md) | 身份识别、能力查询、角度控制、速度控制、传感器数据读取 |
| [L30](./tutorials/l30.md) | 身份识别、能力查询、角度控制、速度控制、传感器数据读取、使能与失能、设备标定 |
| [O30](./tutorials/o30.md) | 身份识别、能力查询、角度控制、速度控制、加速度设置、传感器数据读取、力矩限幅、运动时间设置 |

## 补充说明

- [故障排查](./troubleshooting/README.md)：排查安装、连接、读取和控制问题。
- [更新 SDK](./sdk-update.md)：更新已安装的 SDK。
