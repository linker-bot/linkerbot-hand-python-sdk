# 统一接口迁移说明

统一接口是面向 ROS 和新应用的推荐入口。旧型号类仍可使用，但不应再成为 ROS 的七套适配
目标。迁移可以按生命周期、轮询、命令和状态读取四部分逐步完成。

## 导入与创建

统一类型从独立包导入；`linkerbot.hand` 保留相同名称的懒加载转发：

```python
from linkerbot_hand import CommandPolicy, CommandStage, HandDevice, PollingPlan

# 兼容写法
from linkerbot.hand import CommandPolicy, CommandStage, HandDevice, PollingPlan
```

现有硬件驱动通过 `create_hand_device()` 接入：

```python
from linkerbot.hand import create_hand_device

hand = create_hand_device(
    "L6",
    side="left",
    interface_name="can0",
)
```

与 `L6(...)` 等旧构造器不同，以上代码只创建配置对象，不连接设备。必须显式调用
`open()`。

## API 对照

| 旧型号专用调用                      | 统一接口                                             | 迁移注意事项                                     |
| ----------------------------------- | ---------------------------------------------------- | ------------------------------------------------ |
| `L6(...)`、`L30(...)` 等            | `create_hand_device(model, ...)`                     | 构造无 I/O；在 Lifecycle activate 时 `open()`。  |
| `hand.angle.set_angles(values)`     | `hand.submit_angles(values)`                         | 旧 L30/O20/O30i 入口已弃用且方向不同；需转换值。 |
| `hand.angle.set_raw_angles(values)` | `hand.submit_raw_angles(values)`                     | 值域和单位从能力对象读取。                       |
| `hand.speed.set_speeds(values)`     | `hand.submit_speeds(values)`                         | 连续命令固定 latest-wins。                       |
| `hand.torque.set_torques(values)`   | `hand.submit_torques(values)`                        | 连续命令固定 latest-wins。                       |
| `manager.get_blocking()`            | `start_polling(PollingPlan(...))` + `get_snapshot()` | 周期设备 I/O 只由 SDK 执行。                     |
| `manager.get_snapshot()`            | `snapshot.sample(SensorType...)`                     | 新快照携带时间、序号、有效性、过期和连接代次。   |
| `hand.start_polling({source: 秒})`  | `hand.start_polling(PollingPlan(..._hz=频率))`       | 新计划使用 Hz，不使用秒间隔字典。                |
| `hand.fault.clear()`                | `hand.clear_faults()`                                | 离散操作进入有界 FIFO；先检查能力。              |
| 型号专用异常字符串                  | `HandSDKError` 子类或 `ErrorCode`                    | 禁止按字符串判断故障。                           |

## ROS Lifecycle 映射

推荐在 ROS 驱动中按以下边界管理设备：

| ROS 阶段                     | SDK 操作                                                         |
| ---------------------------- | ---------------------------------------------------------------- |
| `on_configure`               | 创建 `HandDevice`，读取静态 `get_capabilities()`，但不打开设备。 |
| `on_activate`                | `open()`，再按 ROS 参数构造 `PollingPlan` 并 `start_polling()`。 |
| subscription callback        | 仅调用 `submit_*()` 并保存 `CommandHandle`，不调用 `wait()`。    |
| publisher timer              | 仅调用 `get_snapshot()` / `get_health()` 并转换为 ROS 消息。     |
| `on_deactivate`              | `stop_polling()`；不发送自动张开、握拳或清故障动作。             |
| `on_cleanup` / `on_shutdown` | `close()`。                                                      |

ROS 不应再建立任何传感器查询定时器。禁用 SDK 轮询后，只读取快照不会产生查询帧。

## 百分比方向

统一接口中屈伸轴的百分比角度都是 `0=弯曲/闭合，100=伸直/张开`，与型号协议的原始方向
无关。迁移时删除 ROS 内的型号反向、偏移和 `MODEL_SPECS`；使用
`get_capabilities().joints` 与 `percent_to_raw()`。

侧摆、横滚、旋转和手腕不是开合轴，其 `PercentageMapping.semantics` 为 `POSITION`，仍可通过
`submit_angles()` 在完整实测范围内独立控制。此时 0/100 仅表示
`zero_endpoint` / `hundred_endpoint` 对应的物理端点，不能发布成“握拳/张开”。需要协议原值时
使用 `submit_raw_angles()` 和快照 `raw_values`；只有能力明确支持时才能使用物理角度换算。

unified `submit_raw_angles()`、`percent_to_raw()` 和快照 `raw_values` 都使用能力声明的协议
原始坐标。classic 型号旧 manager 的 `set_raw_angles()` 仍会经过历史可配置 LUT，只用于兼容
现有应用；迁移代码不得把该 standard-raw 坐标与 unified raw 混用。

L30、O20、O30i 的旧 `hand.angle.set_angles()` 会发出 `DeprecationWarning`，但为兼容现有应用
仍保留原换算：L30/O20 是 `0=spec.minimum, 100=spec.maximum`，O30i 是
`0=张开, 100=闭合`。迁移到 unified 后必须使用 `100=张开, 0=握拳`，不能直接复用旧百分比。

L30 不按左右手区分角度映射。左手、右手和未知侧能力均发布同一套协议 v6
`raw_angle_range` 和 `percentage_mapping`；连接后识别出的 `side` 只用于设备身份，不会改变
关节范围或角度提交行为。

## 命令完成语义

旧调用返回不等于设备 ACK。迁移后按句柄状态处理：

```python
handle = hand.submit_angles(target, policy=CommandPolicy.LATEST)

# 在非 callback 的监控路径读取
if handle.stage is CommandStage.SUPERSEDED:
    dropped_targets += 1
```

不支持 ACK 的命令以 `DISPATCHED` 为成功终态；只有能力中的
`acknowledged_commands` 明确列出的命令才可能进入 `ACKNOWLEDGED`。机械到位继续由 ROS 根据
状态误差和稳定时间判断。

## 恢复行为

断线后不要在 ROS 层缓存并重放最后目标。调用 `recover()` 或启用 SDK 自动重连即可；SDK 会
清除未发送命令、替换连接、验证身份、读取当前状态并增加连接代次。ROS 应在
`connection_generation` 变化后重新建立自己的状态基线，但不能自动发送断线前目标。

## 建议迁移顺序

1. 将型号和关节配置改为读取 `get_capabilities()`。
2. 把 ROS 周期读取替换为一份 `PollingPlan` 和缓存快照发布。
3. 把运动 callback 替换为 `submit_*()`，删除同步等待。
4. 按命令阶段和稳定错误码改写诊断映射。
5. 把断线恢复交给 `recover()`，删除目标重放与隐式安全动作。
6. 使用 fake backend 覆盖生命周期、超时、覆盖、断线和恢复测试后，再进行实物验证。
