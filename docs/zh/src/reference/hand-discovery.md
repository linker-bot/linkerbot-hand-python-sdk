# 自动发现与身份识别

`discover_hands()` 是显式调用的主动发现函数。导入 `linkerbot_hand`、构造 transport 或
构造 `HandDevice` 都不会扫描总线。默认发现源读取 Linux 中处于 `UP` 状态的 SocketCAN
接口，不执行 `ip link set`，不改变 bitrate、data bitrate、CAN/CAN FD 模式或接口状态。

## 基本调用

```python
from linkerbot_hand import discover_hands

report = discover_hands()
for candidate in report.candidates:
    print(
        candidate.channel.name,
        candidate.channel.kind,
        candidate.wire_protocol,
        candidate.model,
        candidate.side,
        candidate.status,
        candidate.problems,
    )

candidate = report.require_one()
hand = candidate.create_hand_device()
hand.open()
```

发现返回的 transport 已经关闭；候选只保存一个可创建新 transport 的
`TransportSpec`。`create_hand_device()` 也只构造关闭状态的 `HandDevice`，只有随后显式
调用 `hand.open()` 才建立运行连接、验证身份并读取基线状态。

可以限制扫描接口及 L30 Node ID：

```python
from linkerbot_hand import DiscoveryConfig, discover_hands

report = discover_hands(
    DiscoveryConfig(
        channels=("can0", "can1"),
        l30_node_ids=(1, 2, 3),
    )
)
```

## 探测顺序

发现不会把全系列都当作 `0xC0` 协议。实际探针如下：

| 型号 | 线路协议 | 最小身份探针 | 地址范围 |
| --- | --- | --- | --- |
| L6、O6、L20Lite、L20 | classic CAN | `0xC0` 产品码；识别型号后读取 `C0/C1/C2/C4` 完整身份 | 左手 `0x28`、右手 `0x27` |
| O20 | CAN FD | DeviceInfo 寄存器 `0x00` | Device ID `1/2` |
| L30 | CAN FD | 配置对象 DeviceInfo `0x02`，响应后读取 ProductCode `0x03` | Node ID `1..31`，可配置 |
| O30i | CAN FD | HOP ProductInfo `MI 0x41`，随后读取完整身份字段 | 请求 `0x001`、响应 `0x401` |

L20 对外始终报告公共型号 `L20`。发现和身份校验接受常见的 `LHT20-...` 产品编号（例如
`LHT20-10-499-L-B-1-D`），并继续兼容已部署设备上的 `L20-...`、`L25-...`、
`LHL25-...` 和 `LHG20-...` 编号；这些线级前缀不是可传给 `HandDevice` 的型号别名。

已知 nominal bitrate 不是 1 Mbit/s 的接口不会发送 Linkerbot 身份探针；元数据无法提供
bitrate 时则由发现源决定是否允许探测。classic CAN 接口只发送 classic 探针。CAN FD
接口先通过 classic 帧检查 `0x27/0x28`；
只要 classic 身份有响应，就停止该接口上的后续 FD 探测。原因是“网卡支持 CAN FD”和
“设备使用 FD 帧”不是同一件事，而且向包含经典 CAN 节点的总线发送 FD 帧可能触发错误。

只有接口当前配置与型号声明匹配时才发送对应 FD 探针：O20/L30 使用 1 Mbit/s nominal、
5 Mbit/s data，O30i 使用 1 Mbit/s nominal/data。发现不为尝试另一协议而重新配置接口。

## 安全边界

发现过程只发送身份读取，不发送角度、速度、力矩或电流设置，也不执行清错、使能、失能、
标定、参数保存或遥测轮询。L30 的 Node ID 扫描是多个只读 DeviceInfo 请求，不写入地址。

这里的“只读”是 Linkerbot 协议语义，不代表被动监听：发现会主动发送上述身份帧。如果一个
接口还连接了非 Linkerbot 设备，SDK 无法保证对方不会把相同 CAN ID 和载荷解释为其他含义；
混接或用途不明的总线应通过 `DiscoveryConfig(channels=(...))` 明确限定，不应执行全接口扫描。

SDK 实现当前最新协议，不按固件版本预先禁止发现。旧固件不响应、返回空数据或返回不完整
身份时，结果是无候选或 `PARTIAL` 候选，不会因此修改设备或尝试运动。

## 已验证、部分识别与失败

`DiscoveryStatus.VERIFIED` 表示型号、协议要求的身份和端点选择字段均已验证。候选包含
`device_info`、已验证 `capabilities` 以及实际 `wire_protocol`，可直接调用
`create_hand_device()`。

L30 的能力映射不按左右手分叉，因此 `candidate.side` 为 `NOT_APPLICABLE`；产品身份中验证出的
实际左/右手保存在 `candidate.reported_side`，Node ID 保存在 `candidate.address.node_id`。

`DiscoveryStatus.PARTIAL` 表示总线有响应，但证据不足以安全选择。原因位于
`candidate.problems`，例如：

- `identity_timeout`：最小身份有响应，但完整身份字段超时；
- `malformed_identity` / `identity_mismatch`：响应结构或字段互相矛盾；
- `unknown_model`：classic 产品码无法映射到公开型号；
- `address_collision`：同一地址收到重复或冲突身份响应；
- `missing_side`：协议没有给出可验证左右手。

O30i 当前会完整返回型号、UID、协议、硬件、固件、机械和传感器身份，但协议读数不能可靠
验证左右手，因此候选保持 `side=None`。用户确认实物后必须显式解析：

```python
candidate = report.require_one(allow_partial=True)
hand = candidate.create_hand_device(side="right")
```

除仅缺少 `side` 的候选外，部分候选不能创建 `HandDevice`。

无法打开/关闭 transport、发送失败或发现源错误记录在 `report.failures`，与“设备响应但
身份不完整”的候选分开。`report.require_one()` 在无设备、多个候选或唯一候选不完整时分别
抛出 `NoHandFoundError`、`AmbiguousHandError` 或 `IncompleteIdentityError`，不会静默选择。

CAN 无法证明两个完全相同的节点是否在同一时刻发送了逐位相同的帧；如果控制器只交付一帧，
软件没有独立证据区分一个节点和多个相同节点。发现会把可观察到的重复分片、重复完整响应或
冲突数据标为 `address_collision`，但总线拓扑仍应保证固定地址唯一。

## 自定义发现源

默认源是 `SocketCANDiscoverySource`。非 SocketCAN 或厂商适配器可实现公开
`DiscoverySource`：`list_channels()` 只枚举通道，`create_transport()` 按请求的实际线路
协议返回关闭状态 transport。测试也使用同一接口注入内存设备，不需要 ROS 或硬件。
