# Hand SDK 1.0 迁移说明

Hand SDK 1.0 是有意的破坏性版本。新仓库没有保留旧型号实现外壳，因为同时维护型号类、
manager、`HandSession` 和新 actor runtime 会形成两套生命周期、两套状态源和两套测试矩阵。

升级前应在独立分支完成调用迁移和实机回归，不要把新 wheel 当作旧 API 的原位兼容补丁。

## 唯一入口

旧代码：

```python
from linkerbot.hand import L6

hand = L6(side="left", interface_name="can0")
```

新代码：

```python
from linkerbot_hand import HandDevice
from linkerbot_transport.can import CANTransport

hand = HandDevice(
    model="l6",
    side="left",
    transport=CANTransport("can0"),
)
```

已删除：

- 旧版 `from linkerbot.hand import L6, O6, L20lite, L25, O20, L30, O30i` 型号类（其中
  `L25` 是当前公共型号 `L20` 的历史误命名）；
- `from linkerbot import L6`；
- `create_hand_device()`；
- `HandSession`；
- `LegacyHandBackend`；
- 型号 manager、事件流和 manager snapshot；
- `linkerbot.hand` namespace。

安装其他旧发行物后，Python 环境里仍可能存在一个外部 `linkerbot` namespace，但它不由
新 `linkerbot-hand` 提供，也不属于 1.0 兼容契约。

## 方法映射

| 旧调用 | 1.0 调用 | 变化 |
| --- | --- | --- |
| `create_hand_device(...)` / 型号构造 | `HandDevice(model=..., side=..., transport=...)` | transport 与设备协议显式分离 |
| `hand.angle.set_angles(values)` | `hand.set_angles(values)` | 协议原值，非阻塞，返回 handle |
| `submit_angles(values)` | `set_angles(values)` | 删除重复动词 |
| `set_percentage_angles(values)` / `submit_percentage_angles(values)` | `set_percent_angles(values)` | 公开契约统一使用 `percent` |
| `set_raw_angles(...)` / `set_hardware_raw_angles(...)` | `set_angles(...)` | raw 只剩精确协议原值一种 |
| `hand.speed.set_speeds(values)` / `submit_speeds(values)` | `set_speeds(values)` | 配置速度协议值 |
| `submit_torques(values)` | 按 capability 改为 `set_current_limits()`、`set_effort_limits()` 或 `set_torques()` | 不再把限制值统称扭矩 |
| `manager.get_blocking(timeout_ms=...)` | 对应 `get_*(timeout=seconds)` | 主动读取命名统一，timeout 全部为秒 |
| `manager.get_snapshot()` | `hand.get_snapshot()` | 唯一不可变 canonical cache |
| `get_version()` / `hand.version.*` | `get_device_info(timeout)` | 身份和全部可用版本字段统一返回 |
| 旧 polling/event stream | `start_polling(PollingPlan(...))` + `get_snapshot()` | SDK 成为唯一周期查询方 |
| 异常字符串判断 | 捕获 `HandError` 子类或映射 `ErrorCode` | 稳定错误分类 |

## 生命周期迁移

旧型号类可能在构造或 manager 初始化时开始轮询。1.0 必须显式管理：

```python
hand.open()
try:
    hand.start_polling(plan)
    ...
finally:
    hand.stop_polling()
    hand.close()
```

构造后不会连接或查询。`open/close/start/stop` 幂等，`close()` 有界等待 runtime 和
transport 退出。不要依赖 `__del__`，也不要假设 `HandDevice` 支持 `with`。

ROS 2 映射：

| ROS Lifecycle | SDK |
| --- | --- |
| `on_configure` | 构造 transport 和 `HandDevice`，读取 declared capability |
| `on_activate` | `open()`，读取 verified capability，`start_polling()` |
| command callback | 只调用 `set_*()` 并立即返回 |
| publisher/diagnostics | callback 只调用 `get_snapshot()` / `get_health()`；专用发布线程可调用 `wait_for_snapshot()` |
| `on_deactivate` | `stop_polling()` |
| `on_cleanup` / shutdown | `close()` |

ROS 不再创建传感器查询 timer，也不维护 `MODEL_SPECS`。

## 角度数据迁移

旧实现中的“百分比”“标准 raw”“用户 LUT raw”和“hardware raw”可能不是同一坐标。
1.0 只保留：

- `set_angles()` / `AngleSample.raw`：设备协议原值。
- `set_percent_angles()` / `AngleSample.percent`：固定 capability 映射。

屈伸轴的 percent 产品定义为 `0=弯曲，100=伸直`。不要直接把旧百分比数据按相同字段名
回放；先记录旧入口、映射版本、型号、side 和关节顺序，再转换为 1.0 raw 或 percent。

旧 LUT 文件不会读取、迁移或保存。需要业务级标定时，应在应用或标定服务中形成显式版本化
数据，不改变 SDK 的设备协议事实。

## 状态迁移

旧的通用 `SensorSample.values` 和 manager 数据类改为类型化样本：

| 数据 | 1.0 snapshot 字段 |
| --- | --- |
| 角度 | `snapshot.angles.raw / percent / physical_rad` |
| 实测速度 | `snapshot.measured_speeds.raw` |
| 配置速度 | `snapshot.configured_speeds.raw` |
| 实测电流 | `snapshot.currents.raw` |
| 电流上限 | `snapshot.current_limits.raw` |
| 未确认 effort limit | `snapshot.effort_limits.raw` |
| 真实扭矩 | `snapshot.torques.raw`，仅 capability 明确支持时存在 |
| 温度 | `snapshot.temperatures.raw` |
| 触觉/力 | `snapshot.force.raw / tactile` |
| 故障 | `snapshot.faults.raw` |
| 设备信息 | `snapshot.device_info` |
| 堵转配置 | 三个独立 `snapshot.stall_*` 字段 |

每个样本独立携带 `SampleMeta`。不要用 snapshot 的进程读取时间替代设备数据
`collected_at`，也不要把旧连接代次的数据当作有效反馈。

## 命令送达迁移

1.0 setter 的返回值不是设备 ACK：

```python
handle = hand.set_angles(values)
assert handle.stage.value == "accepted"
```

`ACCEPTED` 只表示进入调度器；`DISPATCHED` 才表示 transport 全部发送成功；
`ACKNOWLEDGED` 只用于设备协议明确确认。机械到位必须由上层根据状态反馈判断。

高频目标不要逐条 `wait()`。保存需要追踪的 handle，或消费 command status/health 指标；
ROS callback 中严禁同步等待。

## 升级检查

1. 搜索并删除 `linkerbot.hand`、型号类、manager、`HandSession` 和 `submit_*` 导入。
2. 为每个设备显式创建正确的 CAN/CAN FD transport。
3. 从 capability 获取 joint names、范围、单位、推荐频率和操作支持。
4. 区分 raw、percent、配置值、测量值、电流上限、effort limit 和真实扭矩。
5. 将所有 timeout 从毫秒调用点改为秒制参数。
6. 确保只有 SDK 产生周期设备查询。
7. 为命令阶段、snapshot meta、断线恢复和 unsupported 分支补自动测试。
8. 逐型号、逐轴验证 `set_percent_angles(0/50/100)`、`set_angles()` 和 raw 回读。
9. 在目标 firmware、真实传感器和实际总线负载下重新测量稳定频率上限。
