# 型号能力与运行时查询

`DeviceCapabilities` 是型号、固件、传感器变体、关节顺序、值域和操作支持情况的唯一
事实源。ROS 和应用不能维护另一份 `MODEL_SPECS`。

## 查询

```python
from linkerbot_hand import (
    CommandSemantic,
    HandDevice,
    TelemetrySemantic,
)

declared = hand.get_capabilities()  # open 前：verified=False
hand.open()
verified = hand.get_capabilities()  # open 后：身份和 profile 已验证

angles = verified.command(CommandSemantic.ANGLES)
feedback = verified.telemetry_channel(TelemetrySemantic.ANGLES)

print(verified.identity)
print(verified.joint_names)
print(angles.raw_ranges if angles is not None else None)
print(feedback.recommended_hz if feedback is not None else None)
```

`get_capabilities()` 只读内存，不产生设备 I/O。构造后的 declared capability 可供
ROS `on_configure` 建立关节布局；`open()` 读取身份后才得到
`identity.verified=True` 的实际 profile。

## Capability 结构

| 属性 | 内容 |
| --- | --- |
| `identity` | 型号、左右手、固件、硬件/机械版本、传感器变体、序列号、产品编码、是否验证 |
| `joints` | 稳定 index/name 顺序、运动类型、raw 角度范围、percent 端点、可选物理角度和耦合 |
| `commands` | 命令语义、通道、逐通道 raw 范围、编码、raw 单位/说明、percent 映射、ACK、是否返回当前状态 |
| `telemetry` | 状态语义、读取模式、shape、raw 编码/范围/单位、推荐频率、过期和事务成本 |
| `transport` | CAN/CAN FD、仲裁/数据速率和地址说明 |
| `operations` | 身份读取、清错、使能、失能、标定和堵转配置支持情况 |
| `stall_protection` | 堵转判定时间、阈值、维持电流的 raw 契约和安全范围可信度 |

每个数值事实带有 `Confidence`：`MEASURED`、`DOCUMENTED`、
`APPROXIMATE`、`CONFLICTING` 或 `UNKNOWN`。资料冲突或缺失时，SDK 不生成推测物理值。

## 七型号摘要

下表来自当前 profile 的人工可读摘要。命令和状态名称对应
`CommandSemantic` / `TelemetrySemantic`；精确逐通道范围仍以运行时对象为准。

| 型号 | side | transport | 关节数 | 可写命令 | 可读/缓存状态 | 可选操作 |
| --- | --- | --- | ---: | --- | --- | --- |
| L6 | 必填 | CAN 1M | 6 | angles, speeds, current_limits, 3 项 stall 配置, limit_compensations | angles, configured_speeds, current_limits, force, temperatures, currents, faults, 3 项 stall 配置, limit_compensations | clear_faults, 读写 stall, save_parameters |
| O6 | 必填 | CAN 1M | 6 | angles, speeds, accelerations, current_limits | angles, configured_speeds, configured_accelerations, current_limits, force, temperatures, faults | 无 |
| L20Lite | 必填 | CAN 1M | 10 | angles, speeds, effort_limits | angles, configured_speeds, effort_limits, force, temperatures, faults | 无 |
| L25 | 必填 | CAN 1M | 16 | angles, speeds, effort_limits | angles, configured_speeds, effort_limits, force, temperatures, faults | clear_faults |
| O20 | 必填 | CAN FD 1M/5M | 16 | angles, speeds, effort_limits | angles, measured/configured_speeds, effort_limits, currents, force, temperatures, faults | clear_faults |
| L30 | 禁止 | CAN FD 1M/5M | 17 | angles, speeds, current_limits | angles, measured/configured_speeds, currents, current_limits, force, temperatures, faults | enable, disable, calibrate |
| O30i | 必填 | CAN FD 1M/1M | 20 | angles, speeds, accelerations, effort_limits, motion_times, 3 项 stall 配置 | angles, measured/configured_speeds, configured_accelerations, effort_limits, temperatures, currents, faults, voltages, motion_times, 3 项 stall 配置 | 读写 stall |

`L30` 的 side 为 `NOT_APPLICABLE`，不是 unknown。向 L30 构造传 `left` 或 `right`
会抛出 `ValidationError`。

角度命令的 `returns_current_state` 表示写响应是否已被该型号协议验证为当前实际角度。
L6、O6、L20Lite、L25 当前为 `True`；O20、L30、O30i 为 `False`。后者不代表不能读取
角度，而是写入本身不能代替闭环状态查询。

## 关节顺序

公共向量严格按下列当前 profile 顺序排列：

| 型号 | `joint_names` |
| --- | --- |
| L6 / O6 | thumb_flex, thumb_abd, index, middle, ring, pinky |
| L20Lite | thumb_flex, thumb_roll, index_flex, middle_flex, ring_flex, pinky_flex, index_abd, ring_abd, pinky_abd, thumb_rotation |
| L25 | thumb_rotation, thumb_roll, thumb_root, thumb_tip, index_abd, index_root, index_tip, middle_abd, middle_root, middle_tip, ring_abd, ring_root, ring_tip, pinky_abd, pinky_root, pinky_tip |
| O20 | thumb_mcp, thumb_ip, thumb_roll, thumb_rotation, index_abd, index_mcp, index_pip, middle_abd, middle_mcp, middle_pip, ring_abd, ring_mcp, ring_pip, pinky_abd, pinky_mcp, pinky_dip |
| L30 | thumb_root, thumb_tip, thumb_abd, thumb_rotation, ring_abd, ring_tip, ring_root, middle_root, middle_tip, pinky_root, pinky_tip, pinky_abd, middle_abd, index_abd, index_root, index_tip, wrist |
| O30i | thumb_rotation, thumb_roll, index_abd, middle_abd, ring_abd, pinky_abd, thumb_root1, index_root1, middle_root1, ring_root1, pinky_root1, index_root2, middle_root2, ring_root2, pinky_root2, thumb_tip, index_tip, middle_tip, ring_tip, pinky_tip |

不要按关节数猜顺序，也不要把线上 reserved slot 暴露为公共关节。O20 公共接口只有 16 个
实际自由度；O30i 的 36 槽协议对象由 profile 映射为 20 个安装关节。

## 推荐轮询频率

推荐频率是 profile 元数据，不会自动启动轮询。当前主要通道摘要：

| 型号 | angles | force | temperatures | currents | faults |
| --- | ---: | ---: | ---: | ---: | ---: |
| L6 | 60 Hz | 5 Hz | 1 Hz | 10 Hz | 5 Hz |
| O6 | 60 Hz | 30 Hz | 1 Hz | 不支持 | 5 Hz |
| L20Lite | 60 Hz | 30 Hz | 1 Hz | 不支持 | 5 Hz |
| L25 | 60 Hz | 30 Hz | 1 Hz | 不支持 | 5 Hz |
| O20 | 30 Hz | 30 Hz | 1 Hz | 10 Hz | 5 Hz |
| L30 | 60 Hz | 30 Hz | 1 Hz | 10 Hz | 5 Hz |
| O30i | 30 Hz | 不支持 | 1 Hz | 10 Hz | 5 Hz |

设备实测速度推荐频率为 O20/L30/O30i 30 Hz。配置速度不是实测速度：

- L6 设置速度后由设备回显配置值，来源为 `DEVICE_CONFIG_RESPONSE`；协议未明确支持只发
  `0x05` 主动查询，因此该通道是 `COMMAND_RESPONSE`，不是实测速度，也不是主机推测缓存。
- O6 查询返回配置参数，发布到 `configured_speeds`。
- O20、L30、O30i 提供独立 `measured_speeds`。

运行时会在 health 中报告 requested/achieved Hz、missed deadlines 和 timeout。推荐值不是
实机稳定上限；上限必须在相同工作量和总线配置下测试。

## 电流、effort 与扭矩

三种限制不能混用：

- L6/O6 使用 `current_limits`，协议原值为 count；L6 的文档比例为
  `raw × 1400 / 255 mA`，比例只记录在 capability metadata 中。
- L30 使用 `current_limits`，协议值本身以 mA 表示。
- L20Lite/L25/O20/O30i 的相关字段在当前契约中是 `effort_limits`，物理含义未完全确认。
- 当前七型号没有 profile 声明真实 `torques` 通道，因此
  `set_torques()` / `get_torques()` 会返回 unsupported。

`raw_unit` 表示返回数字本身的协议单位。若协议数字是 count，即使资料提供
“0.732 rpm/count”或近似 mA 比例，SDK 仍返回 count；比例只在
`raw_scale_note` 与 `unit_confidence` 中说明。

## 百分比与物理角度

七型号的 angle command 都有逐关节 `PercentMapping`。屈伸轴使用统一产品契约
`0=弯曲，100=伸直`；侧摆、旋转和腕轴通过 `zero_label` / `hundred_label` 描述端点。

当前速度命令没有 profile 声明可验证的 percent mapping，因此
`set_percent_speeds()` 目前会抛出 unsupported。只有将来某个 profile 明确给出端点、
方向和量化规则时才会开放。

`PhysicalAngleMapping` 只有在真实物理标定可信时存在。没有映射时
`AngleSample.physical_rad` 对应轴为 `None`，不能把协议 count 或 percent 伪装成弧度。

## 身份与 firmware profile

`open()`、`get_device_info()` 和 `recover()` 共用同一身份验证边界。验证内容包括设备可
提供的型号、左右手、固件、硬件/机械版本、传感器变体、序列号和产品编码。

例如 O6 `04-B` profile 使用五指 `10x4` 触觉。L6 不设置最低固件连接门槛；旧固件可连接，
可选查询首次超时或结构不符时只禁用该通道并抛出 `UnsupportedFirmwareError`，其他能力保持
运行。身份变化仍抛出 `IdentityMismatchError`。L6 逐接口和版本证据见
[L6 型号文档](./models/l6.md) 与 [L6 固件支持证据](./models/l6-firmware-support.md)。
