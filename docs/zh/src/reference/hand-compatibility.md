# 手部 SDK 接口边界

`linkerbot-hand` 同时提供规范的 `linkerbot_hand` 统一入口和
`linkerbot.hand` 型号驱动入口。两者共享同一数值原则：简短的默认控制方法与反馈返回
协议值，百分比或物理量转换必须显式命名。

## 包边界

| 发行包 | 职责 | 依赖边界 |
| --- | --- | --- |
| `linkerbot-transport` | CAN/CAN FD、错误、队列、relay 和连接原语 | 不依赖手部、ROS 或运动学 |
| `linkerbot-hand` | 统一接口、能力、调度、快照、fake 工具和七型号驱动 | 依赖 transport，不依赖 ROS |

两个 wheel 共同组成 `linkerbot` namespace，但不拥有共享的 `linkerbot` 包根。
`from linkerbot import L6` 应改为 `from linkerbot.hand import L6`。

## 保留内容

- `from linkerbot.hand import L6, O6, L20lite, L25, L30, O20, O30i`。
- 型号 manager、阻塞读取、事件流和显式轮询入口。
- `create_hand_device()` 以及统一的 `HandDevice` / `HandSession`。
- 构造器中的显式 transport、轮询和周期上报配置。

`auto_start_polling` 与 `auto_start_periodic` 默认关闭。ROS Lifecycle 集成必须自行拥有
activate/deactivate 边界，不应依赖构造器隐式启动。

## 不兼容内容

本仓库不兼容旧的用户角度映射机制，以下行为和名称已删除：

- 用户可配置角度映射表及其持久化依赖；
- `set_raw_angles()`、`set_hardware_raw_angles()`、`submit_raw_angles()`；
- 独立的原始反馈副本；
- 默认接口中的百分比、RPM 或 mA 自动换算。

替代规则如下：

| 需求 | 当前接口 |
| --- | --- |
| 精确发送协议位置 | `set_angles()` / `submit_angles()` |
| 精确发送协议速度 | `set_speeds()` / `submit_speeds()` |
| 发送百分比位置 | `set_percentage_angles()` / `submit_percentage_angles()` |
| 发送百分比速度 | `set_percentage_speeds()` / `submit_percentage_speeds()`，先检查 capability |
| 读取协议反馈 | manager 数据对象或 `SensorSample.values` |
| 获取单位和编码 | `SensorSample.unit` / `encoding`，或 capability |

这是面向遥操与训练数据一致性的有意破坏性变更，不提供旧行为兼容层。迁移细节见
[统一协议值接口迁移说明](./hand-migration.md)。

型号 manager 的显式百分比 helper 与统一 capability 百分比在部分型号/轴上方向不同；
这不影响默认协议值契约。跨入口复用数据时以协议值为边界，具体规则见
[协议值、百分比与显式换算](./angle-coordinates.md)。

## 错误与会话边界

统一接口公开稳定的 `HandSDKError` 子类和 `ErrorCode`。兼容后端会将型号驱动异常
归一化；调用方不应解析错误消息字符串。

不要让型号 manager 和统一 session 同时控制或轮询同一设备。一个 transport 连接只能有
一个 I/O 所有者，否则命令调度、阻塞响应匹配和快照时序都无法保证。

兼容承诺覆盖公开导入路径、当前协议值 API 和发行 wheel，不覆盖私有属性、内部线程名、
异常文本或已删除的旧映射行为。
