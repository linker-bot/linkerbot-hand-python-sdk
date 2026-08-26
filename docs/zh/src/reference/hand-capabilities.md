# 七型号能力矩阵

本页是 `linkerbot-hand 0.1` 的人工可读能力摘要。程序必须以运行时
`hand.get_capabilities()` 为准，不应复制本表形成另一份 `MODEL_SPECS`。连接成功后，SDK 会用
后端读取的身份补充 `firmware` 和 `side`；协议不能独立报告左右手时，`side` 保留调用方配置，
未连接时两者可能为 `None` / `unknown`。

## 总览

传感器单元格格式为 `名称@推荐 Hz`。推荐频率不是隐式轮询配置，只有显式传给
`PollingPlan` 的非零频率才会产生查询。

| 型号    | transport | 关节数 | 传感器与推荐频率                                                                   | 可选操作         | 明确设备 ACK               |
| ------- | --------- | -----: | ---------------------------------------------------------------------------------- | ---------------- | -------------------------- |
| L6      | CAN       |      6 | 角度@60、力矩@30、力@30、温度@1、电流@10、故障@5                                   | 清故障           | 无                         |
| O6      | CAN       |      6 | 角度@60、速度@30、加速度@10、力矩@30、力@30、温度@1、故障@5                        | 无               | 无                         |
| L20Lite | CAN       |     10 | 角度@60、速度@30、力矩@30、力@30、温度@1、故障@5                                   | 无               | 无                         |
| L25     | CAN       |     16 | 角度@60、速度@30、力矩@30、力@30、温度@1、故障@5                                   | 清故障           | 无                         |
| L30     | CAN FD    |     17 | 角度@60、速度@30、力@30、温度@1、电流@10、故障@5                                   | 使能、禁用、标定 | 使能、禁用、标定           |
| O20     | CAN FD    |     16 | 角度@30、速度@30、力@30、温度@1、电流@10、故障@5                                   | 清故障           | 无                         |
| O30i    | CAN FD    |     20 | 角度@30、速度@30、加速度@10、力矩@30、温度@1、电流@10、故障@5、电压@1、运动时间@10 | 无               | 角度、速度、力矩           |

所有型号都提供非阻塞的协议角度、速度和力矩提交入口；具体值域见下表。传感器矩阵
表示可轮询的反馈，不等同于命令支持。例如某型号可设置速度但不提供速度反馈。

## 关节顺序

| 型号    | 协议顺序中的关节名称                                                                                                                                                                                                     |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| L6      | `thumb_flex`, `thumb_abd`, `index`, `middle`, `ring`, `pinky`                                                                                                                                                            |
| O6      | `thumb_flex`, `thumb_abd`, `index`, `middle`, `ring`, `pinky`                                                                                                                                                            |
| L20Lite | `thumb_flex`, `thumb_abd`, `index_flex`, `middle_flex`, `ring_flex`, `pinky_flex`, `index_abd`, `ring_abd`, `pinky_abd`, `thumb_yaw`                                                                                     |
| L25     | `thumb_abd`, `thumb_yaw`, `thumb_root1`, `thumb_tip`, `index_abd`, `index_root1`, `index_tip`, `middle_abd`, `middle_root1`, `middle_tip`, `ring_abd`, `ring_root1`, `ring_tip`, `pinky_abd`, `pinky_root1`, `pinky_tip` |
| L30     | `j1` 至 `j17`                                                                                                                                                                                                            |
| O20     | `thumb_mcp`, `thumb_ip`, `thumb_abd`, `thumb_cmc`, `index_abd`, `index_mcp`, `index_pip`, `middle_abd`, `middle_mcp`, `middle_pip`, `ring_abd`, `ring_mcp`, `ring_pip`, `pinky_abd`, `pinky_mcp`, `pinky_dip`            |
| O30i    | `roll_0`, `yaw_0` 至 `yaw_4`, `root1_0` 至 `root1_4`, `root2_1` 至 `root2_4`, `tip_0` 至 `tip_4`                                                                                                                         |

## 控制范围与单位

| 型号    | 协议位置范围             | 协议速度范围   | 协议力矩/限制范围                   | 物理角度换算             |
| ------- | ------------------------ | -------------- | ----------------------------------- | ------------------------ |
| L6      | 每轴 `0..255 count`      | `0..255 count` | `0..255 count`，`1400/255 mA/count` | 不支持                   |
| O6      | 每轴 `0..255 count`      | `0..255 count` | `0..255 count`                      | 不支持                   |
| L20Lite | 每轴 `0..255 count`      | `0..255 count` | `0..255 count`                      | 不支持                   |
| L25     | 每轴 `0..255 count`      | `0..255 count` | `0..255 count`                      | 不支持；仅有逐轴物理端点 |
| L30     | 逐轴见下表，`count` 整数 | `1..250 count` | `60..800 count`，`6.5 mA/count`     | 不支持，不得视为度或弧度 |
| O20     | 逐轴见下表，`count` 整数 | `0..100 count` | `0..1000 count`，`6.5 mA/count`     | 不支持；角度比例有冲突   |
| O30i    | 每轴 `0..255 count`      | `0..255 count` | `0..255 count`                      | 不支持                   |

L30 不按左右手区分角度映射。`side=left`、`side=right` 和 `side=unknown` 均使用以下同一套
原始位置范围与百分比映射；设备上报的左右手信息只更新能力中的身份字段，不改变关节能力。

| 关节  | 范围      | 关节  | 范围        | 关节  | 范围      |
| ----- | --------- | ----- | ----------- | ----- | --------- |
| `j1`  | 0..900    | `j2`  | 0..1200     | `j3`  | 0..900    |
| `j4`  | 0..800    | `j5`  | -200..200   | `j6`  | 0..1500   |
| `j7`  | 0..1600   | `j8`  | 0..1600     | `j9`  | 0..1500   |
| `j10` | 0..1600   | `j11` | 0..1500     | `j12` | -200..200 |
| `j13` | -200..200 | `j14` | -200..200   | `j15` | 0..1600   |
| `j16` | 0..1500   | `j17` | -1000..1000 |       |           |

命令范围由 `JointCapability` 声明，反馈单位与编码由 `SensorCapability` 声明。默认接口
不做物理量换算。L6 能设置速度但协议没有当前速度查询，因此不包含
`SensorType.SPEEDS`；`hand.speed.get_snapshot()` 只返回最近一次设置的协议目标。

共享的反馈标量范围保存在 `SensorCapability.value_range`，其单位始终与 `unit` 一致。
L30、O20 的角度反馈按逐关节范围解释，因此不发布一个会掩盖轴间差异的共享范围；调用方
应读取对应的 `JointCapability.raw_angle_range`。

O20 速度反馈为 `count` / `i16_le`，比例为 `0.732 rpm/count`；需要 RPM 时显式调用
`protocol_speeds_to_rpm()`。L30 的“扭矩”字段实际是协议电流上限，范围 `60..800`；
电流反馈为 `count` / `i16_be`。二者每协议计数对应 6.5 mA，物理量换算必须显式调用
相应的 `*_to_milliamps()` 函数。L30 速度反馈同为 `i16_be`，比例为
`0.732 rpm/count`。

O6 加速度反馈保留 `0..254` 协议值，其中 `0` 特殊表示最大加速度；需要度/秒²时使用
`O6Acceleration` 的显式 helper。O30i 的角度、速度、加速度、转矩、温度、电流和电压均只
发布 `0..255 count`，不将它们伪装成 RPM、mA、V 或摄氏度；运动时间是唯一已确认线性比例的
运行时量，为 `10 ms/count`。

O20 协议位置范围：

| 关节         | count   | 关节         | count   |
| ------------ | ------- | ------------ | ------- |
| `thumb_mcp`  | 0..120  | `thumb_ip`   | 0..150  |
| `thumb_abd`  | 0..180  | `thumb_cmc`  | 0..130  |
| `index_abd`  | -30..30 | `index_mcp`  | 0..180  |
| `index_pip`  | 0..180  | `middle_abd` | -30..30 |
| `middle_mcp` | 0..180  | `middle_pip` | 0..180  |
| `ring_abd`   | -20..20 | `ring_mcp`   | 0..180  |
| `ring_pip`   | 0..180  | `pinky_abd`  | -20..20 |
| `pinky_mcp`  | 0..180  | `pinky_dip`  | 0..180  |

O20 协议文档一处将当前位置写为 `0.087°/count`，但目标位置范围和实测方向资料使用
`0..120`、`0..180` 等协议端点，无法与该比例一致对应。capability 因此仅声明
`physical_unit="degree"`，`scale/offset` 和物理端点保持 `None`，不得把 count 直接标为度。

## 反馈编码与物理元数据

`unit` 描述 `SensorSample.values`，原始协议计数统一为 `count`。只有 `scale` 和 `offset`
同时存在时才可使用 `physical = raw * scale + offset`；`physical_unit` 单独存在只说明物理
维度已知。触觉点始终按标量 `u8` 编码，矩阵结构由 `value_kind=tactile_matrix` 与
`matrix_shape` 描述。

| 型号 | 数据源 | `unit` / `encoding` | 物理元数据 | 值语义 |
| ---- | ------ | ------------------- | ---------- | ------ |
| L6 | 角度 | `count` / `u8` | 未知 | `numeric` |
| L6 | 力矩限制、当前电流 | `count` / `u8` | `mA`，`scale=1400/255`，`offset=0` | `numeric` |
| L6 | 温度 | `degree_celsius` / `u8` | 同原值，`scale=1`，`offset=0` | `numeric` |
| L6 | 触觉 | `count` / `u8` | 未标定 | `tactile_matrix`，每指 `12x6` |
| L6 | 故障 | `dimensionless` / `u8` | 无 | `bitmask`，bit7 保留但原值不丢失 |
| O6 | 角度 | `count` / `u8` | 未知 | `numeric` |
| O6 | 速度 | `count` / `u8` | `rpm`，比例冲突，`scale=None` | `numeric` |
| O6 | 力矩限制 | `count` / `u8` | `mA`，比例冲突，`scale=None` | `numeric` |
| O6 | 加速度 | `count` / `u8` | `degree_per_second_squared`，比例冲突；raw 0 为特殊最大值 | `numeric` |
| O6 | 温度 | `degree_celsius` / `u8` | 同原值，`scale=1`，`offset=0` | `numeric` |
| O6 `04-B` | 触觉 | `count` / `u8` | 未标定 | `tactile_matrix`，每指 `10x4` |
| O6 | 故障 | `dimensionless` / `u8` | 无 | `bitmask`，保留位原值不丢失 |
| L20Lite | 角度、速度、力矩 | `count` / `u8` | 未知 | `numeric` |
| L20Lite | 温度 | `degree_celsius` / `u8` | 同原值，`scale=1`，`offset=0` | `numeric` |
| L20Lite | 触觉 | `count` / `u8` | 未标定 | `tactile_matrix`，每指 `12x6` |
| L20Lite | 故障 | `dimensionless` / `u8` | 无 | `bitmask`，保留位原值不丢失 |
| L25 | 角度 | `count` / `u8` | `degree`，逐轴只有端点，`scale=None` | `numeric` |
| L25 | 速度 | `count` / `u8` | 逐轴为 `rpm`、`millimeter_per_second` 或未知，`scale=None` | `numeric` |
| L25 | 力矩限制 | `count` / `u8` | `mA`，逐轴只有端点，`scale=None` | `numeric` |
| L25 | 温度 | `degree_celsius` / `u8` | 同原值，`scale=1`，`offset=0` | `numeric` |
| L25 | 触觉 | `count` / `u8` | 未标定 | `tactile_matrix`，每指 `12x6` |
| L25 | 故障 | `dimensionless` / `u8` | 无 | `bitmask` |
| L30 | 角度 | `count` / `i16_be` | 未知 | `numeric` |
| L30 | 速度 | `count` / `i16_be` | `rpm`，`scale=0.732`，`offset=0` | `numeric` |
| L30 | 当前电流 | `count` / `i16_be` | `mA`，`scale=6.5`，`offset=0` | `numeric` |
| L30 | 温度 | `degree_celsius` / `u8` | 同原值，`scale=1`，`offset=0` | `numeric` |
| L30 | 触觉 | `count` / `u8` | 未标定 | `tactile_matrix`，每指 `12x6` |
| L30 | 故障 | `dimensionless` / `u8` | 无 | `bitmask` |
| O20 | 角度 | `count` / `i16_le` | `degree`，比例冲突，`scale=None` | `numeric` |
| O20 | 速度 | `count` / `i16_le` | `rpm`，`scale=0.732`，`offset=0` | `numeric` |
| O20 | 当前电流 | `mA` / `i16_le` | 同原值，`scale=1`，`offset=0` | `numeric` |
| O20 | 温度 | `count` / `u8` | 协议未给单位 | `numeric` |
| O20 | 触觉 | `count` / `u8` | 未标定 | `tactile_matrix`，每指 `12x6` |
| O20 | 故障 | `dimensionless` / `u8` | 无；有效值 `0..4` | `enum` |
| O30i | 角度、速度、加速度、转矩、电流、电压、温度 | `count` / `u8` | 比例未知 | `numeric` |
| O30i | 运动时间 | `count` / `u8` | `millisecond`，`scale=10`，`offset=0` | `numeric` |
| O30i | 故障 | `dimensionless` / `u8` | 无；bit 定义未确认 | `bitmask`，保留完整原始字节 |

编码含义：`u8` 是无符号 8 位整数；`u16_le` 是无符号 16 位小端；`i16_le` 是有符号
16 位小端；`i16_be` 是有符号 16 位大端。O30i 主角度 capability 对应 `MI=0x01` 的
`u8` 快照；实验性的 `hand.angle.get_u16_blocking()` 单独读取 `MI=0x20` 完整 72 字节
`u16_le` 对象，不替代主快照接口。

## 百分比映射

默认协议值与显式百分比换算的完整调用关系见
[协议值、百分比与显式换算](./angle-coordinates.md)。

屈伸轴的公共约定为 `0=弯曲/闭合，100=伸直/张开`。方向轴也提供可逆 `0..100` 位置，但其
两端由 capability 描述，不能解释成开合。`submit_angles()` 与快照 `values` 都使用协议
坐标，可以精确往返；`submit_percentage_angles()` 才执行 capability 声明的线性换算。

百分比速度还要求 `percentage_speed_supported=True`。目前 L6、O6、L20Lite 和 L25
支持；L30、O20 和 O30i 不支持。O20 的协议范围 `0..100` 不能据此解释为百分比。

| 型号    | 屈伸轴                                           | `POSITION` 方向轴                                                  |
| ------- | ------------------------------------------------ | ------------------------------------------------------------------ |
| L6 / O6 | `thumb_flex`, `index`, `middle`, `ring`, `pinky` | `thumb_abd`：靠近虎口 -> 远离虎口                                  |
| L20Lite | `thumb_flex`、四指 `*_flex`                      | `thumb_abd` 横滚、三指侧摆、`thumb_yaw` 旋转                       |
| L25     | 各手指 root/tip                                  | 拇指旋转/横滚及四指侧摆                                            |
| L30     | `j1/j2/j6..j11/j15/j16`                          | `j3/j4`、`j5/j12..j14` 侧摆、`j17` 手腕                            |
| O20     | MCP/IP/PIP/DIP 屈伸轴                            | `thumb_abd` 横滚、`thumb_cmc` 旋转及四指侧摆；拇指两轴声明机械耦合 |
| O30i    | `root1_*`, `root2_*`, `tip_*`                    | `roll_0` 拇指旋转、`yaw_0` 拇指横滚、`yaw_1..4` 四指侧摆           |

每个 `PercentageMapping` 的协议端点完整保存在 `raw_at_zero/raw_at_hundred`，物理端点保存在
`zero_endpoint/hundred_endpoint`。兼容字段 `raw_closed/raw_open` 与前两者值相同，但在
`POSITION` 轴上名称不代表开合。调用方还必须先检查 `raw_angle_range` / `percentage_mapping`
本身是否为 `None`；这表示位置或映射尚未验证，而不是数值为零。

### 实测归一化端点

下表用于人工核对。程序应读取每个关节的 capability，不应复制这些规则：

| 型号/关节类型             | 0% 的协议值与物理端点         | 100% 的协议值与物理端点       |
| ------------------------- | ----------------------------- | ----------------------------- |
| L6 / O6 屈伸              | raw 0，弯曲                   | raw 255，伸直                 |
| L6 / O6 拇指侧摆          | raw 0，靠近虎口               | raw 255，远离虎口             |
| L20Lite 屈伸              | raw 0，弯曲                   | raw 255，伸直                 |
| L20Lite 拇指横滚          | raw 0，垂直掌心               | raw 255，平行掌心             |
| L20Lite 三指侧摆          | raw 0，靠近中指               | raw 255，远离中指             |
| L20Lite 拇指旋转          | raw 0，靠近食指               | raw 255，远离食指             |
| L25 屈伸                  | raw 0，弯曲                   | raw 255，伸直                 |
| L25 拇指旋转/横滚         | raw 0，靠近食指/垂直掌心      | raw 255，远离食指/平行掌心    |
| L25 四指侧摆              | raw 0，向小指侧               | raw 255，向拇指侧             |
| O20 屈伸                  | 各轴 raw 最大值，弯曲         | 各轴 raw 最小值，伸直         |
| O20 四指侧摆              | raw 最小值，远离拇指          | raw 最大值，靠近拇指          |
| O20 拇指横滚/旋转         | raw 最小值，平行掌心/远离食指 | raw 最大值，垂直掌心/靠近食指 |
| O30i 屈伸                 | raw 255，弯曲                 | raw 0，伸直                   |
| O30i 拇指旋转/横滚        | raw 0，远离食指/平行掌心      | raw 255，靠近食指/垂直掌心    |
| O30i 四指侧摆             | raw 0，靠近拇指               | raw 255，远离拇指             |
| L30 屈伸                  | 各轴 raw 最大值，弯曲         | 各轴 raw 最小值，伸直         |
| L30 `j3/j4`               | raw 最小值，远离虎口/远离掌心 | raw 最大值，靠近虎口/靠近掌心 |
| L30 `j5/j12/j13/j14` 侧摆 | raw 最小值，向小指侧          | raw 最大值，向食指侧          |
| L30 `j17` 手腕            | raw -1000，向前               | raw 1000，向后                |

O20 的 `thumb_abd` 和 `thumb_cmc` 在 capability 中分别标记为横滚与旋转，并通过
`coupled_with` 互相引用。两者协议值可以独立收发，但精确 FK、IK、URDF 或末端姿态控制必须考虑
真实机械耦合，不能把它们当成两个完全独立的固定欧拉角。

## 能力查询示例

```python
caps = hand.get_capabilities()

for joint in caps.joints:
    print(
        joint.index,
        joint.name,
        joint.raw_angle_range,
        joint.percentage_mapping,
    )

for sensor in caps.sensors:
    print(
        sensor.sensor.value,
        sensor.unit,
        sensor.encoding,
        sensor.physical_unit,
        sensor.scale,
        sensor.offset,
        sensor.value_kind.value,
        sensor.matrix_shape,
    )

print(caps.features.clear_faults)
print(caps.features.acknowledged_commands)
```

不要仅检查 `device_ack` 后假设全部命令有 ACK；必须使用
`caps.features.supports_ack(command_kind)` 做逐命令判断。
