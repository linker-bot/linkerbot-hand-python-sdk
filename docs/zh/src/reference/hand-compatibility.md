# 手部 SDK 兼容策略

`linkerbot-hand` 是独立发行包，同时提供规范的 `linkerbot_hand` 入口和历史
`linkerbot.hand` 入口。兼容层让 ROS 和新应用使用统一接口，并允许旧型号专用应用分阶段迁移。

## 包边界

| 发行包                | 职责                                                                                | 运行时依赖边界                                        |
| --------------------- | ----------------------------------------------------------------------------------- | ----------------------------------------------------- |
| `linkerbot-transport` | CAN/CAN FD、稳定错误分类、队列、数据 relay、连接生命周期和共享租约                  | 不依赖手部、机械臂、ROS、NumPy 或运动学               |
| `linkerbot-hand`      | 统一接口、能力、调度、快照、健康、fake 工具、七型号驱动及 `linkerbot.hand` 兼容路径 | 依赖 transport；不依赖 ROS、机械臂、运动学或聚合 SDK |

只使用灵巧手时安装 `linkerbot-hand`，其依赖会自动安装 `linkerbot-transport`。两个 wheel
共同组成 `linkerbot` namespace，但不拥有共享的 `linkerbot` 包根文件，因此不会与后续独立的
机械臂包争用同一文件。

## 保留的导入路径

统一 API 的规范导入路径是 `linkerbot_hand`。迁移期内旧包路径继续转发同一对象：

```python
from linkerbot.hand import HandDevice, HandSession, PollingPlan, CommandPolicy
```

现有 `from linkerbot.hand import L6, L30, ...` 和 `from linkerbot.hand.<model> import ...`
继续可用。类、函数、方法签名和返回类型保持不变，兼容转发采用延迟导入，不会仅因导入统一
类型而加载全部型号驱动。

旧聚合发行包曾额外提供 `from linkerbot import L6`。独立 wheel 不能安全拥有这个共享包根，
否则会与 transport 和后续 arm wheel 发生文件覆盖；该写法应机械替换为
`from linkerbot.hand import L6`，类和后续调用不变。

## 行为兼容

- 旧 manager API（`hand.angle`、`hand.speed`、`get_blocking()`、旧事件流等）保留给现有
  应用，新 ROS 适配必须使用统一接口。
- L6、O6、L20Lite、L25 构造器的 `auto_start_polling` 默认值为 `False`；确有旧行为依赖的
  应用可临时显式传 `True`。
- L30 / L30Bus 的 `auto_start_periodic` 默认值为 `False`；需要设备端周期上报的旧应用应显式
  传 `True` 或调用启动方法。
- 统一接口固定 `100=张开，0=握拳`。旧型号专用接口的历史原始值和映射接口继续保留；其中
  L30、O20、O30i 的旧 `hand.angle.set_angles()` 已弃用，调用时发出 `DeprecationWarning`，但本
  版本不改变其数值或发送行为。
- unified `submit_raw_angles()` 和快照 `raw_values` 使用协议线上原始值，并绕过 classic 旧
  manager 的 standard-raw LUT；旧 `set_raw_angles()` 的映射语义保持不变。
- `linkerbot.hand` 继续提供 `create_hand_device()`，把现有七型号驱动适配到统一接口。

显式启用旧自动轮询参数仅用于过渡；在 ROS Lifecycle 集成中不得使用，因为 activate 与
deactivate 必须拥有轮询的完整控制权。

旧百分比入口的保留语义如下。`DeprecationWarning` 默认不会显示，应用可在迁移测试中启用该
类别以定位调用点：

| 旧入口                    | 保留语义                             | 替代入口                     |
| ------------------------- | ------------------------------------ | ---------------------------- |
| `L30.angle.set_angles()`  | `0=spec.minimum`，`100=spec.maximum` | `HandDevice.submit_angles()` |
| `O20.angle.set_angles()`  | `0=spec.minimum`，`100=spec.maximum` | `HandDevice.submit_angles()` |
| `O30i.angle.set_angles()` | `0=张开`，`100=闭合`                 | `HandDevice.submit_angles()` |

替代入口统一使用 `100=张开，0=握拳`。迁移时不能只替换方法名，必须按能力映射调整目标值；需要完全
保留设备原始坐标的代码应迁移到 `submit_raw_angles()`。

## 错误兼容

统一接口公开稳定的 `HandSDKError` 子类和 `ErrorCode`。兼容后端负责把旧驱动异常归一化；
调用统一接口的代码只捕获 `linkerbot_hand` 错误，不解析旧异常消息。

继续直接调用旧型号类的应用可以保留 `linkerbot.exceptions` 捕获逻辑。两套调用方式不应在同一
设备会话中混用；尤其不能由旧 manager 和统一 session 同时写总线或轮询传感器。

## 版本与弃用

- `linkerbot-transport` 与 `linkerbot-hand` 使用独立版本号，hand 通过依赖范围声明兼容的
  transport 版本。每个仓库独立构建、打标签和发布，未修改的包不需要同步递增版本。
- 当前发布只新增统一入口并改变隐式轮询默认值，不删除旧型号公开名称。
- L30、O20、O30i 的旧百分比 `set_angles()` 已进入弃用窗口；删除版本尚未设定，其它旧 manager
  若未来弃用，也会先在代码和 CHANGELOG 中给出警告并至少保留一个次版本的迁移窗口。
- 能力字段采用增加式演进。调用方应按字段和枚举判断支持情况，不按型号名称硬编码能力。
- 任何方向、单位、ACK 或错误分类的语义修正都记录在 CHANGELOG，并配套兼容测试。

## 支持范围

兼容承诺覆盖公开 API 和发行 wheel，不覆盖私有属性、未导出 manager 内部类、异常文本或线程
名称。测试注入应使用公开的 backend protocol 与 `linkerbot_hand.testing`，不 monkey-patch
设备私有成员。
