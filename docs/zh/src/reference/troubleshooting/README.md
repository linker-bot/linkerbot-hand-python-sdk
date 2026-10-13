# 故障排查

## 导入失败

请确认安装 SDK 和运行程序使用同一个 Python 环境。

```bash
# 查看当前 Python 路径
python -c "import sys; print(sys.executable)"
# 查看 SDK 导入路径
python -c "import linkerbot_hand; print(linkerbot_hand.__file__)"
```

如果提示缺少模块，请激活安装时使用的环境，再按[安装说明](../quick-start.md)安装依赖。

## 未找到设备

请核对接口名称、总线类型和速率，并关闭占用同一通道的其他程序。Linux SocketCAN 需要先[配置并启用接口](../connection/prepare.md)。

手动连接时，请核对型号和左右手。自动连接时，请检查 `report.failures` 的错误码和消息：

```python
# report 为 discover_hands() 返回的扫描报告
for failure in report.failures:
    print(failure.code, failure.message)
```

MiniCANFD 需安装对应驱动和动态库。Linux 默认先检查通道 `0`，无候选且关闭成功后再检查 `1`；`no_hand_found` 表示检查完成仍无手。缺库、占用和清理失败请按报告分别处理，具体配置见[自动连接](../connection/automatic.md#minicanfd)。

Windows SLCAN 自动枚举仅选择 `16D0:117E`，其他 USB 标识需指定串口；确认 CAN FD 固件和串口速率，默认串口速率为 `115200`。Linux 的 CAN / CAN FD 接入请确认驱动或固件提供了相应的 SocketCAN 接口。

发现多台设备时，请使用自动连接页面的多台连接示例；单台示例要求只发现一台设备。

## 连接或读取超时

请核对接口、型号和左右手，并关闭占用同一通道的其他程序。连接设置见[手动连接](../connection/manual.md)。

读取方法中的 `timeout` 以秒为单位。设备响应较慢时，可以适当增加等待时间；持续超时则应检查通信配置。

## 控制失败

请确认已经调用 `hand.open()`，并按[对应型号的功能说明](../README.md#models)检查控制模式、参数范围和数值顺序。

命令已发送不代表设备已经到达目标位置。请使用对应的读取方法确认设备状态；原始角度值和角度百分比应使用各自对应的设置方法。

[返回总览](../README.md)
