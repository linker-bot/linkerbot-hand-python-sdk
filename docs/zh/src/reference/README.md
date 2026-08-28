# linkerbot-hand API Reference

本目录描述当前 `linkerbot-hand` 的唯一公开 API。所有型号都通过
`linkerbot_hand.HandDevice` 使用；型号类、manager、`HandSession`、
`create_hand_device()` 和 `linkerbot.hand` 已删除。

## 阅读顺序

1. [统一 HandDevice API](./hand-device.md)：构造、生命周期、命令、主动读取、轮询、
   快照、健康状态和错误。
2. [型号能力](./hand-capabilities.md)：七型号的 transport、关节、命令、状态与可选操作。
3. [角度与值语义](./angle-coordinates.md)：协议原值、`percent`、方向和物理角度边界。
4. [经典 CAN](./can.md) / [CAN FD](./canfd.md)：Linux 总线配置和 transport 构造。
5. [迁移说明](./hand-migration.md)：从已删除的旧型号类和双接口迁移。
6. [型号协议能力](./models/README.md)：逐型号维护接口、opcode、单位、解释、固件证据和限制。

程序必须以 `hand.get_capabilities()` 返回的当前 profile 为准。文档矩阵用于阅读和评审，
不能复制到 ROS 或应用中形成另一份 `MODEL_SPECS`。

## 唯一导入

```python
from linkerbot_hand import AngleFeedbackPolicy, HandDevice, PollingPlan
```

内部模块 `linkerbot_hand.protocols` 和 `linkerbot_hand.runtime` 不属于稳定用户入口。
测试注入可以使用公开构造参数，但应用不应直接操作 protocol transaction 或 runtime actor。
