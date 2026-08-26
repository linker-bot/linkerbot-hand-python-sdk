# 版本信息

获取 O6 灵巧手的版本信息。

## 概述

通过 `hand.version` 访问版本管理功能：

- 获取设备完整信息（序列号、PCB/固件/机械版本）
- 从出厂编码解析产品版本、左右手和传感器类型

## 获取设备信息

```python
hand.version.get_device_info() -> DeviceInfo
```

返回 `DeviceInfo` 对象，包含：

| 属性                 | 类型      | 说明                    |
| -------------------- | --------- | ----------------------- |
| `serial_number`      | `str`     | 设备序列号              |
| `pcb_version`        | `Version` | PCB 硬件版本            |
| `firmware_version`   | `Version` | 固件版本                |
| `mechanical_version` | `Version` | 机械结构版本            |
| `timestamp`          | `float`   | 获取时间（Unix 时间戳） |
| `serial_identity`    | `O6SerialIdentity \| None` | 出厂编码解析结果 |

`serial_identity` 提供 `product_version`、`hand_side`、`sensor_type`、
`sensor_revision`、`factory_code`、`capability_variant` 和
`force_sensor_shape`。例如：

```text
LHO6-04.1-730-R-B-1-D
       │       │ │
       │       │ └─ B 型点阵触觉
       │       └─── 右手
       └─────────── 产品版本 04.1
```

当前已验证的 `04-B` 变体使用 `B1..B5 A4` 查询，每根手指返回一个
`10x4` 点阵。统一接口在 `open()` 后根据该身份结果更新 `FORCE` capability。

`Version` 对象包含 `major`、`minor`、`patch` 属性，字符串格式为 `V{major}.{minor}.{patch}`。

**异常**：

- `TimeoutError`：请求超时

## 示例

### 读取设备信息

```python
from linkerbot.hand import O6

hand = O6(side="left", interface_name="can0")

info = hand.version.get_device_info()
print(f"序列号：{info.serial_number}")
print(f"PCB 版本：{info.pcb_version}")
print(f"固件版本：{info.firmware_version}")
print(f"机械版本：{info.mechanical_version}")
if info.serial_identity is not None:
    print(f"产品版本：{info.serial_identity.product_version}")
    print(f"左右手：{info.serial_identity.hand_side}")
    print(f"传感器类型：{info.serial_identity.sensor_type}")

hand.close()
```
