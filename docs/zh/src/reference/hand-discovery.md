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

最后三行是三个独立步骤：扫描得到信息、按信息构造关闭状态的设备、显式打开连接。详见下一节。

可以限制扫描接口、L30 Node ID 及 O30 标准帧请求 ID：

```python
from linkerbot_hand import DiscoveryConfig, discover_hands

report = discover_hands(
    DiscoveryConfig(
        channels=("can0", "can1"),
        l30_node_ids=(1, 2, 3),
        o30_request_ids=(0x001, 0x002, 0x123),
    )
)
```

O30 默认只探测 `0x001`、`0x002`；已改地址的设备应显式加入
`o30_request_ids`（`0x000..0x3FE`），不会广播或遍历整个地址空间。
`candidate.create_hand_device()` 保留实际发现的请求/回复 ID，左右手来自身份字段；
请求 ID 本身不能证明侧别。无侧别信息的候选仍需调用方明确指定。

已知 O30 地址时也可直接构造，不必运行发现：

```python
from linkerbot_hand import DeviceConfig, HandDevice

hand = HandDevice(
    model="O30",
    side="left",
    transport=transport,
    config=DeviceConfig(o30_request_id=0x123),
)
```

这里仅配置 SDK 端点，不会写设备 CAN ID；回复 ID 固定为 `request_id | 0x400`。
`o30_request_id=None` 使用对应侧别的默认地址；其他型号不接受该配置。
扩展帧地址和特殊广播 ID 不属于此入口。普通身份刷新保留同一连接的触觉元数据，
重连或识别到身份变化后重新读取。当前实机与最新表格同为 249 字节身份布局，
SDK 按字段内容识别布局，不按固件版本号拒绝连接。

## 返回什么

`discover_hands()` 返回的是一份**信息报告**，不是设备对象。报告只有四样内容：

| 字段 | 内容 |
| --- | --- |
| `report.channels` | 本次扫描覆盖了哪些接口 |
| `report.candidates` | 每个应答的端点一条记录：接在哪个 CAN 口、什么型号、左手右手、支持哪些方法；另附序列号、各版本号与内部选路信息 |
| `report.failures` | 接口打不开、发送失败或发现源自身出错；与「设备响应但身份不完整」的候选分开记录 |
| `report.elapsed_s` | 本次扫描耗时 |

`candidates` 里全是不可变数据类，不含任何 `HandDevice`。得到设备对象需要三步，每步都由调用方显式发起：

| 步骤 | 产出 | 是否产生设备 I/O |
| --- | --- | --- |
| `discover_hands()` | 信息报告 | 是，只发身份读取；返回前关闭所有探测 transport |
| `candidate.create_hand_device()` | **关闭状态**的 `HandDevice` | 否，纯内存构造 |
| `hand.open()` | 已连接、已验证身份的设备 | 是，建连、读身份、读基线 |

三步互相解耦：可以只跑第一步做现场勘查；也可以完全跳过前两步，直接用 `HandDevice(model=..., side=..., transport=...)` 走第三步。发现**不会**替调用方创建对象、打开设备、启动轮询或在多个候选中挑选。

### 单个接口与多个接口

`candidates` 是一个**扁平元组**，跨所有接口拉平，按接口名排序；不按接口分组嵌套。区分来自哪个接口要读 `candidate.channel.name`。

| 场景 | `candidates` | `require_one()` |
| --- | --- | --- |
| 一个接口一只手 | 长度 1 | 返回该候选 |
| 三个接口各一只手 | 长度 3 | 抛 `AmbiguousHandError` |
| 接口正常但无设备应答 | 空 | 抛 `NoHandFoundError` |
| 接口打不开 | 空，原因在 `failures` | 抛 `NoHandFoundError` |

`require_one()` 是**唯一性断言**，不是创建入口：它只在候选恰好一个时返回，绝不静默挑选。因此多设备场景不要用它，直接遍历：

```python
report = discover_hands()

hands = {
    candidate.channel.name: candidate.create_hand_device()
    for candidate in report.verified_candidates
}
for name, hand in hands.items():
    hand.open()
```

`verified_candidates` 会自动排除身份不完整的端点；想知道有没有半残设备，再看 `report.partial_candidates` 与 `report.failures`。

## 候选里要用的四样信息

一条候选虽然带了很多字段，但正常流程只需要读四样：

| 要知道的事 | 读哪个字段 | 说明 |
| --- | --- | --- |
| 接在哪个 CAN 口 | `candidate.channel.name` | 例如 `can0`；`channel.kind` 是接口模式，`wire_protocol` 是实际线路协议 |
| 什么型号 | `candidate.model` | 公开型号名，例如 `O30`、`L20`；线级工厂前缀已归一 |
| 左手还是右手 | `candidate.side` | `LEFT` / `RIGHT`；L30 不分左右手，为 `NOT_APPLICABLE` |
| 支持哪些方法 | `candidate.capabilities` | **`open()` 之前就能查**，见下 |

`capabilities` 在 VERIFIED 候选上一定存在且 `identity.verified=True`，所以可以在决定要不要连、连了之后能干什么之前就查清楚：

```python
caps = candidate.capabilities
print(caps.joint_names)  # 关节顺序
print([command.semantic.value for command in caps.commands])  # 可写命令
print([channel.semantic.value for channel in caps.telemetry])  # 可读状态
print(caps.operations)  # 清错、使能、标定等
```

其余字段各有用途但不在正常流程里：序列号、产品编号与各版本号用于记录和排查；`address`、`transport`、`raw_identity`、`fingerprint` 是发现内部选路与去重用的，`create_hand_device()` 会自己消费它们，应用不需要读，也不应基于它们自行拼装设备。

`candidate.reported_side` 是设备**单独上报的物理侧字段**，与 `candidate.side` 不是同一件事：判断「用哪个 side 建设备」一律看 `side`，`reported_side` 只作为设备自报证据。L30 是两者不一致的典型，说明见下文「已验证、部分识别与失败」。

## 探测顺序

发现不会把全系列都当作 `0xC0` 协议。实际探针如下：

| 型号 | 线路协议 | 最小身份探针 | 地址范围 |
| --- | --- | --- | --- |
| L6、O6、L20Lite、L20 | classic CAN | `0xC0` 产品码；识别型号后读取 `C0/C1/C2/C4` 完整身份 | 左手 `0x28`、右手 `0x27` |
| O20 | CAN FD | DeviceInfo 寄存器 `0x00` | Device ID `1/2` |
| L30 | CAN FD | 配置对象 DeviceInfo `0x02`，响应后读取 ProductCode `0x03` | Node ID `1..31`，可配置 |
| O30 | CAN FD | HOP ProductInfo `MI 0x41`，随后读取完整身份字段 | 每侧一对固定 ID：右手请求 `0x001`、响应 `0x401`；左手请求 `0x002`、响应 `0x402`。两对都探测 |

L20 对外始终报告公共型号 `L20`。发现和身份校验接受常见的 `LHT20-...` 产品编号（例如
`LHT20-10-499-L-B-1-D`），并继续兼容已部署设备上的 `L20-...`、`L25-...`、
`LHL25-...` 和 `LHG20-...` 编号。

O30 对外始终报告公共型号 `O30`。发现和身份校验接受 `O30`、`O30-...`，也兼容历史的
`O30i`、`O30i-...` 产品编号，不区分大小写。L20 与 O30 都在 `product_code` 中原样保留
设备上报的编号，`candidate.model` 和 capability 的 `model` 则始终使用规范型号。

产品编号兼容与配置型号分开：`HandDevice` 和 `get_declared_capabilities()` 的 `model`
应分别填写 `l20`、`o30`（不区分大小写）。历史名称 `l25`、`o30i`、其他工厂前缀及完整
产品编号均不能作为配置值；传入这些值会在设备 I/O 前抛出 `ValidationError`。

已知 nominal bitrate 不是 1 Mbit/s 的接口不会发送 Linkerbot 身份探针；元数据无法提供
bitrate 时则由发现源决定是否允许探测。classic CAN 接口只发送 classic 探针。CAN FD
接口先通过 classic 帧检查 `0x27/0x28`；
只要 classic 身份有响应，就停止该接口上的后续 FD 探测。原因是“网卡支持 CAN FD”和
“设备使用 FD 帧”不是同一件事，而且向包含经典 CAN 节点的总线发送 FD 帧可能触发错误。

只有接口当前配置与型号声明匹配时才发送对应 FD 探针：O20/L30 要求 1 Mbit/s nominal 与 5 Mbit/s data；O30 只要求 1 Mbit/s nominal，它的 profile 不声明 data bitrate（BRS off，数据段恒等于标称速率），因此接口 dbitrate 取任意值都会被探测。发现不为尝试另一协议而重新配置接口。

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

O30 会完整返回型号、UID、协议、硬件、固件、机械和传感器身份，并从身份字段解析左右手，因此候选通常是完整的、`side` 已确定，直接创建即可：

```python
candidate = report.require_one()
hand = candidate.create_hand_device()
```

不要写死 `side`：候选已有 `side` 时传入不同值会抛 `ValidationError`，写死 `side="right"` 的代码遇到左手 O30 会直接失败。若确实要显式传，必须与发现结果一致。

左右手同时挂在同一总线时，两个端点都会返回候选，`report.require_one()` 会抛 `AmbiguousHandError`；此时按 `candidate.side` 或 `candidate.address` 从 `report.candidates` 中自行选择。

只有固件未提供左右手字段时，O30 候选才会带 `missing_side` 问题、`side` 为 `None`，这种情况需要在确认实物后显式传入：

```python
candidate = report.require_one(allow_partial=True)
hand = candidate.create_hand_device(side="left")
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
