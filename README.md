# linkerbot-hand

`linkerbot-hand` 是 Linkerbot 全系列灵巧手的独立 Python SDK。L6、O6、
L20Lite、L20、O20、L30 和 O30 共用唯一的 `HandDevice` 接口；型号差异由
`get_capabilities()` 返回，不再公开型号类、manager、`HandSession` 或
`linkerbot.hand` 兼容入口。

英文说明见 [README.en.md](./README.en.md)，完整中文 reference 见
[docs/zh/src/reference](./docs/zh/src/reference/README.md)。

## 设计边界

- 构造 `HandDevice` 和 transport 时零设备 I/O、零线程、零运动。
- `open()`、`close()`、`start_polling()` 和 `stop_polling()` 显式且幂等。
- `set_*()` 非阻塞，返回 `CommandHandle`；连续目标使用 latest-wins。
- 普通 `set_*()` / `get_*()` 使用设备协议原值，不做隐式物理量换算。
- `set_speeds()` 使用设备协议原值。
- `set_percent_angles()` 使用 `0..100`；屈伸轴统一为
  `0=弯曲/闭合，100=伸直/张开`。
- 查询型 `get_*()` 主动执行一次新事务；声明为 `COMMAND_CACHE` 或
  `COMMAND_RESPONSE` 的 getter 只返回明确标注来源的主机缓存或设备写响应。
  `get_snapshot()` 和 `get_health()` 始终只读内存；专用消费线程可用
  `wait_for_snapshot()` 等待缓存 revision，不产生设备 I/O。
- SDK 是设备周期查询的唯一所有者，ROS 或应用只提交 `PollingPlan`。
- 核心包不依赖 NumPy、SciPy、ROS、机械臂或旧聚合 SDK。

## 快速启动

开发分支新增[统一触觉字段接口](./docs/zh/src/reference/force.md)：
`hand.get_force(timeout=1.0)` 默认读取可用 `fn + fns`，
`hand.get_force("fn", timeout=1.0)` 按类别选择，返回 `sample.fn.raw` 等原始值和单位。
此接口尚未包含在下方安装示例的已发布 2.0.0 中。

| 型号 | `fn` 点阵 | `fns` 合力/汇总 | `ft/fts/fd/fds/px` |
| --- | --- | --- | --- |
| L6 / O6 / L20 / L30 / O20 | 支持 | 不支持 | 不支持 |
| L20Lite | 支持 | `0x20` 五指原始汇总 | `fts/fds/px` 支持（`0x21/22/23`）；`ft/fd` 不支持 |
| O30 | 按 descriptor | 按 descriptor | 按 descriptor，没有字段时不伪造 |

`PollingPlan(force_fn_hz=20)` 沿用现有调度器；`force_hz` 保留为默认两字段的频率简写。
O30 单独选 fn 或 fns 都读取完整原生流，两者设置不同频率时取最大值，只查询一次。
`hand.get_capabilities().force_field("fn")` 返回支持情况、共享查询与单位说明。

Hand SDK 的唯一直接运行依赖是 `linkerbot-transport`；transport 的 `[all]` extra 会安装
CAN/CAN FD 所需的 `python-can`。Hand 不依赖 ROS、机械臂、运动学或聚合 SDK。以下命令
适用于 Ubuntu，创建独立环境、显式安装两层 Linkerbot 包并完成无硬件验证：

```bash
sudo apt update
sudo apt install -y python3-venv can-utils

python3 -m venv ~/venvs/linkerbot-hand-2.0.0
source ~/venvs/linkerbot-hand-2.0.0/bin/activate
python -m pip install --upgrade pip
python -m pip install \
  --index-url https://gitea.linkerhub.work/api/packages/zhuzhixiang/pypi/simple \
  --extra-index-url https://pypi.org/simple \
  'linkerbot-transport[all]==1.0.0' \
  'linkerbot-hand==2.0.0'

python - <<'PY'
from importlib.metadata import version

from linkerbot_hand import HandDevice, SUPPORTED_MODELS
from linkerbot_transport.can import CANTransport

print("linkerbot-transport", version("linkerbot-transport"))
print("linkerbot-hand", version("linkerbot-hand"))
print("models", ", ".join(SUPPORTED_MODELS))

transport = CANTransport("can0")
hand = HandDevice(model="l6", side="left", transport=transport)
print("lifecycle", hand.get_health().connection_state.value)
hand.close()
PY
```

预期最后四行包含 transport `1.0.0`、hand `2.0.0`、七个支持型号和 `lifecycle closed`。构造 transport
和 `HandDevice` 不会打开 `can0`、创建线程或产生设备 I/O，因此该验证不要求连接实物。
新终端继续使用时先执行：

```bash
source ~/venvs/linkerbot-hand-2.0.0/bin/activate
```

只有开发尚未发布的源码时才使用 editable 安装。注意 `linkerbot-transport` 不在公共 PyPI 上，因此**不能只安装 hand** —— 单独执行 `pip install -e .` 会因解析不到 transport 而失败。两种可行写法：

同时从源码安装两层（开发 transport 时用这个）：

```bash
git clone ssh://git@git.linkerhub.work:12222/zhuzhixiang/linkerbot-transport.git
git clone ssh://git@git.linkerhub.work:12222/zhuzhixiang/linkerbot-hand.git
python -m pip install -e './linkerbot-transport[all]' -e ./linkerbot-hand
```

只改 hand、transport 用已发布版本（更常见）：

```bash
python -m pip install \
  --index-url https://gitea.linkerhub.work/api/packages/zhuzhixiang/pypi/simple \
  --extra-index-url https://pypi.org/simple \
  -e .
```

两种写法都可用同一条命令验证装到的是源码而非已发布包：

```bash
python -c 'import linkerbot_hand; print(linkerbot_hand.__file__)'
```

输出应指向本地工作区路径，而不是 `site-packages`。

Python 要求 `>=3.10`。`linkerbot-hand` 已声明对 transport 的运行依赖；快速启动仍显式
列出两者，是为了让首次安装的依赖版本和排障边界清晰可见。

## SocketCAN 准备

经典 CAN 型号 L6、O6、L20Lite、L20 使用 1 Mbit/s：

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up
```

L30 和 O20 使用 1 Mbit/s 仲裁速率、5 Mbit/s 数据速率：

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000 dbitrate 5000000 fd on
sudo ip link set can0 up
```

O30 使用 1 Mbit/s 仲裁速率；BRS off，数据段恒等于标称速率、接口 dbitrate 不影响通信，因此**推荐 5 Mbit/s，但任意数据段速率都可用**（1M、2M、7M 等均可）：

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000 dbitrate 5000000 fd on
sudo ip link set can0 up
```

详细配置见 [经典 CAN](./docs/zh/src/reference/can.md) 和
[CAN FD](./docs/zh/src/reference/canfd.md)。

## 唯一调用方式

设备型号、左右手或接口未知时，可显式执行只读身份发现：

```python
from linkerbot_hand import discover_hands

report = discover_hands()
candidate = report.require_one()
hand = candidate.create_hand_device()
```

上例用于单手。多只手接在不同接口时，直接使用命名字典，无需调用 `require_one()`：

```python
from linkerbot_hand import discover_hands

report = discover_hands()
named = report.named_candidates()  # 例如 l6l、l6r、o6l、o6r
hands = report.create_hand_devices()  # 仅 VERIFIED 候选，全部为关闭状态
```

同型号同侧别有多只时使用 `l6l1`、`l6l2` 等编号，L30 依据已验证的物理侧别使用
`l30l` / `l30r`。名称是本次扫描的别名，设备增减时可能改变。无已验证候选时返回空字典；
打开设备前仍需检查报告的部分身份、失败及清理问题，详见发现文档。

候选工厂会保存已有身份指纹，首次 `open()` 及重试必须与扫描身份一致；例如扫描 L30
左手后在同一通道换成右手，会拒绝打开。更换设备或改变指纹中的版本信息后需重新扫描、
构造；具体边界见[身份绑定说明](./docs/zh/src/reference/hand-discovery.md#扫描身份与首次打开)。

`discover_hands()` 按平台组合发现源：Linux 使用 SocketCAN 和 MiniCANFD，Windows 使用 PCAN、SLCAN 和 MiniCANFD。SocketCAN 只读取已启用接口，不改变系统配置；USB 后端需要安装驱动，Linux MiniCANFD 还需提供已确认的通道布局。PCAN 默认探测经典 CAN，SLCAN 与 MiniCANFD 默认探测 CAN FD。配置、依赖要求和错误处理见 [自动发现与身份识别](./docs/zh/src/reference/hand-discovery.md)。

探测只读身份，不启动轮询或发送运动、清错、标定命令。当前维护范围为一通道一只手，多只手使用不同接口。多个候选、同地址冲突或身份不完整时不会静默选择。O30 的左右手由身份字段给出，候选的 `side` 已确定，不要写死 `side`。

经典 CAN 的 L6、O6、L20Lite、L20 优先通过 `0xFF#C0` 查询产品码，无回复或缺片时依次回退 `0x27/0x28`；两种分片编号均可解析，后续连接保留真实默认地址，非默认地址以部分候选报告。

已知型号和接口时仍可直接构造统一设备类：

```python
from linkerbot_hand import AngleFeedbackPolicy, HandDevice, PollingPlan
from linkerbot_transport.can import CANTransport

transport = CANTransport("can0")
hand = HandDevice(
    model="o6",
    side="right",
    transport=transport,
)

hand.open()
try:
    capabilities = hand.get_capabilities()
    print(capabilities.identity)
    print(capabilities.joint_names)

    hand.start_polling(
        PollingPlan(
            angles_hz=60,
            force_hz=30,
            temperature_hz=1,
            faults_hz=5,
            angle_feedback=AngleFeedbackPolicy.ADAPTIVE,
        )
    )

    handle = hand.set_percent_angles([100, 50, 100, 100, 100, 100])
    print(handle.command_id, handle.stage, handle.ack_support)

    fresh = hand.get_angles(timeout=0.2)
    print(fresh.raw)
    print(fresh.percent)

    snapshot = hand.get_snapshot()
    health = hand.get_health()
    print(snapshot.angles)
    print(health.connection_state)
finally:
    hand.stop_polling()
    hand.close()
```

默认 `ADAPTIVE` 会在型号 profile 已验证角度设置返回当前状态时使用该样本，并在
`angles_hz` 不足时主动补询；不返回当前角度的型号则退化为纯主动查询。
`QUERY_ONLY` 强制周期主动查询，`COMMAND_FEEDBACK_ONLY` 只适用于 capability 明确声明
`returns_current_state=True` 的型号。三种策略都不改变显式 `get_angles()` 的主动读取语义，
SDK 也不会自动重复最后一个运动目标。详见
[统一 HandDevice API](./docs/zh/src/reference/hand-device.md)。

`set_percent_angles()` 会产生运动。实机首次验证时应使用当前姿态附近的小幅目标，并先确认
型号、左右手、关节顺序和百分比端点。

L30 设备会报告左右手，但当前控制能力不按侧别分叉，构造时不得传 `side`：

```python
from linkerbot_hand import HandDevice
from linkerbot_transport.canfd import SocketCANFDTransport

l30 = HandDevice(
    model="l30",
    transport=SocketCANFDTransport(
        "can0",
        bitrate=1_000_000,
        data_bitrate=5_000_000,
    ),
)
```

## 值语义

```python
# 以下 `hand` 指上文已经 open 的六轴 O6 设备。
raw_handle = hand.set_angles([255, 128, 255, 255, 255, 255])
percent_handle = hand.set_percent_angles([100, 50, 100, 100, 100, 100])

angles = hand.get_angles(timeout=0.2)
print(angles.raw)  # 设备协议原值
print(angles.percent)  # SDK 固定 capability 映射
print(angles.physical_rad)  # 无可靠物理标定的轴为 None
```

速度配置、实测速度、电流上限、实测电流、转矩上限和转矩数据是不同通道。
电流统一用 `get_currents()` / `set_current_limits()`；支持限制回读的型号提供
`get_current_limits()`。L20 无限制配置 getter；L20Lite 的限制和速度配置 getter 仅读取
本连接内完整写回显（`COMMAND_RESPONSE`），不发送裸查询。O6 的 `0x02` 查询返回当前电流，
使用 `get_currents()` / `snapshot.currents` / `PollingPlan(current_hz=...)`；
写入回复为限制设定值，`get_current_limits()` 仅读取本连接的写回复缓存，不发送查询。
O30 另有独立的 `get_torques()` 和
`set/get_torques_limits()`。数据保留协议原值，单位和比例以 capability 为准，不根据名称换算。
调用前应检查 `capabilities.command(...)` 和
`capabilities.telemetry_channel(...)`；不支持的统一方法会抛出
`UnsupportedOperationError`。

L6 V1.0.2 另提供 `set_torques()` / `get_torques()`（`0x03` 力矩出力及模式），
以及 `set/get_position_kp()`、`set/get_position_kd()`、`set/get_torque_kd()`。
`0x03` 每关节写 0 切回位置模式，1..255 切入力矩模式并设置出力；查询值的实测力矩
语义与物理比例尚未确认，能力标记 `device_measured=False`。`get_currents()` 仍是
`0x36` 实测电流，`set/get_current_limits()` 仍是 `0x02` 电流上限。
原值范围、增益比例、模式切换等待和实机响应差异
见 [L6 接口说明](./docs/zh/src/reference/models/l6.md)；其他型号未声明这些新命令时返回不支持。

## 命令阶段

`CommandHandle` 至少区分：

- `ACCEPTED`：已通过校验并进入有界调度器。
- `DISPATCHED`：transport 已成功发送该命令的全部帧。
- `ACKNOWLEDGED`：设备协议返回了可匹配的明确 ACK。
- `FAILED`：编码、发送、协议或连接失败。
- `SUPERSEDED`：发送前被同命令族的新目标覆盖。

不支持设备 ACK 的型号以 `DISPATCHED` 作为成功终态。该阶段不表示机械到位。

## 测试

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest
ruff check src/linkerbot_hand tests/hand
ruff format --check src/linkerbot_hand tests/hand
ty check src/linkerbot_hand tests/hand/api_v1
python -m build
```

实机测试需要先确认型号、固件、供电、终端电阻和总线配置。L6/O6 不设置最低固件连接
门槛；旧固件缺少可选查询时，SDK 只禁用对应通道并抛出
`UnsupportedFirmwareError`，不会断开其他可用功能。详见
[逐型号接口与固件证据](./docs/zh/src/reference/models/README.md)。

## 文档

- [统一 HandDevice API](./docs/zh/src/reference/hand-device.md)
- [自动发现与身份识别](./docs/zh/src/reference/hand-discovery.md)
- [型号能力与查询方式](./docs/zh/src/reference/hand-capabilities.md)
- [协议原值、percent 与角度方向](./docs/zh/src/reference/angle-coordinates.md)
- [破坏性迁移说明](./docs/zh/src/reference/hand-migration.md)
- [逐型号接口、单位与协议说明](./docs/zh/src/reference/models/README.md)
- [版本变更记录](./CHANGELOG.md)

所有面向用户的功能、行为、接口、依赖或文档变更，都必须在同一个 PR 中更新
`CHANGELOG.md` 的“未发布”章节。

本仓库只包含 Hand SDK。机械臂、运动学和 Pinocchio 属于独立 Arm SDK；ROS 2 消息、
Lifecycle、QoS 和 topic 映射属于 `linkerbot-hand-ros`。
