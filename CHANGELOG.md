# Changelog

本项目遵循语义化版本。正式发布前的开发版本可能继续增加兼容修正。

## 未发布

- 将“角度设置返回当前角度”建模为全系列统一事件；各协议 profile 通过
  `returns_current_state` 明确声明并独立处理 CAN/CAN FD 标识、单帧/多帧关联和 ACK，
  公共层不假设 O6 帧格式，也不把无应答、目标值回显或 ACK 冒充当前状态。
- `PollingPlan.angle_feedback` 新增默认 `ADAPTIVE`、`QUERY_ONLY` 和
  `COMMAND_FEEDBACK_ONLY` 三种角度反馈策略；自适应模式只对命令反馈采样缺口主动查询。
- 分离“事务等待响应”和“设备协议 ACK”契约，不支持 ACK 的命令即使等待状态回显，
  成功终态仍为 `DISPATCHED`。
- angle health 增加命令反馈、有效反馈样本和反馈策略指标，显式 `get_angles()` 始终保持
  主动查询语义。

## 1.0.0.dev0 - 2026-08-28

- 删除 `linkerbot.hand` 七型号类、manager、`HandSession`、legacy backend 和重复
  runtime，只保留 `linkerbot_hand.HandDevice` 一种公开调用方式。
- 普通 `set_*()` / `get_*()` 统一表示协议原值；百分比接口统一使用
  `set_percent_*()` 命名，删除 LUT、标准 raw、hardware raw 和 `submit_*` 别名。
- capability registry 只返回 Hand 1.0 不可变类型，不再生成旧 contract 兼容视图。
- 使用单 actor runtime 统一生命周期、命令、主动读取、轮询、缓存和安全恢复；同一物理
  CAN/CAN FD 通道只保留一个连接、RX owner 和有界 TX arbiter。
- 将 L6、O6、L20Lite、L25、O20、L30 和 O30i 全部迁移到独立协议 profile，并由
  capability 表达型号、关节、传感器、命令和反馈差异。
- health 区分逻辑读取完成、缓存更新来源与 ROS 发布所需指标；transport 流量统一按共享
  物理通道累计帧数和 DLC 数据区字节数。
- 核心发行依赖移除 NumPy，并将 transport 依赖限制为 CAN/CAN FD extras；wheel 不再包含
  `linkerbot` namespace，sdist 不再携带内部测试。
- 重写 README、API reference 和破坏性迁移说明，删除只能调用旧 manager 的型号文档。

## 0.2.1 - 2026-08-26

- 解析 O6 `0xC0` 出厂编码中的产品版本、左右手和传感器类型；
  `LHO6-04.1-730-R-B-1-D` 识别为右手 `04-B` 变体。
- 明确 O6 `04-B` 使用 `B1..B5 A4` 查询五指 `10x4` 点阵触觉，并在统一
  capability 中只对已实现的 04 系列传感器变体声明触觉支持。
- 连接和安全恢复后均重新应用设备身份对应的 capability，ROS 继续直接消费 SDK
  能力描述，无需维护独立型号规则。

## 0.2.0 - 2026-08-26

- 将 `set_angles()` / `submit_angles()`、`set_speeds()` /
  `submit_speeds()` 统一为协议原始值接口；命令值不再隐式归一化或换算物理单位。
- 新增明确命名的 `set_percentage_angles()` / `submit_percentage_angles()`；L6、O6、
  L20Lite 和 L25 还支持显式百分比速度接口。
- `SensorSample.values` 只包含协议解码值，并补充 `unit`、`encoding`、`physical_unit`、
  `scale`、`offset`、`value_kind` 与 `matrix_shape`；删除重复的原始值通道，
  `JointState.angle` 也直接保存协议位置。
- 删除用户角度映射实现、持久化依赖、旧 raw-angle 方法和兼容别名。本仓库不兼容旧映射
  行为。
- 将 RPM、mA 和物理角度转换保留为显式纯函数；O20 的 `0..100` 速度仍按协议范围处理，
  不声明为百分比。
- 基于完整协议资料补全 L6、O6、L20Lite、L25、L30、O20、O30i 的原始编码、物理单位、
  已确认比例/偏移、故障值语义和触觉矩阵尺寸；资料冲突时不发布推测比例。
- 修正 L30 反馈编码为大端 `i16_be`，保留经典手故障字节的保留位，并让协议 `u8`
  温度保持整数类型。
- 修正 O30i 方向轴的百分比换算与关节物理语义，并将 `0x20` 高分辨率位置按协议定义的
  无符号小端 `u16_le` 解码；主反馈继续返回 `0..255 count`，仅运动时间发布
  `10 ms/count` 的已确认比例。
- 扩展 `.gitignore`，覆盖常见 Python 构建、测试、类型检查、虚拟环境、编辑器和本地配置
  产物，并忽略本地 `superpowerdocs/` 协议资料目录。

## 0.1.3 - 2026-08-25

- L6、O6、L20Lite 和 L25 的多帧序列号读取同时兼容连续帧号
  `0、1、2、3` 与字节偏移 `0、6、12、18` 两种位置编码。
- 兼容 L6 V2.3.3 返回三个连续满帧且不携带终止符的序列号响应。
- 严格拒绝缺失中间帧、非法位置和旧事务残片，避免使用不完整或过期数据完成
  设备身份验证。

## 0.1.2 - 2026-08-25

- 修正无依据的 `numpy>=2.2.6` 下限，Python 3.10-3.12 支持 NumPy 1.21.6 至 1.x，
  Python 3.13 及以上支持 NumPy 2.1 至 2.x。
- ROS 2 Jazzy 的 Python 3.12 环境默认解析到 NumPy 1.26.x，避免与系统 SciPy 1.11.4
  的 `numpy<1.28` 约束冲突。
- 增加依赖标记回归测试，并在 NumPy 1.x 和 2.x 环境中验证相同 SDK 功能。

## 0.1.1 - 2026-08-25

- L6 的力矩限制统一使用 mA，`0–255` 协议值映射为约 `0–1400 mA`；速度仅保存已设置目标，不伪造当前速度反馈。
- O6 的速度、力矩限制和加速度分别统一为 RPM、mA 和度/秒²，并保留协议量化转换。
- L30 的扭矩设置明确为电流上限并使用 mA，当前电流反馈统一为 mA。
- O20 的当前速度统一为 RPM；目标扭矩保留 `0–1000` 整数范围并明确每单位 `6.5 mA`。
- L20Lite、L25 的速度和扭矩继续明确为无量纲值。
- 增加物理单位、量化边界和兼容入口的自动化回归测试。

## 0.1.0 - 2026-08-17

- 独立提供 `linkerbot_hand` 统一设备接口和 `linkerbot.hand` 兼容入口。
- 包含 L6、O6、L20Lite、L25、L30、O20 和 O30i 驱动与能力描述。
- 使用独立 `linkerbot-transport` 包提供 CAN/CAN FD、错误和并发原语。
- 保留统一百分比、协议原始值、非阻塞调度、缓存快照、健康状态和安全恢复契约。
- 角度映射配置遵循 `XDG_CONFIG_HOME`，未设置时继续使用 `~/.config`。
- 默认 README 提供完整中文说明，并保留独立英文版本。
