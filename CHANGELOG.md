# Changelog

本项目遵循语义化版本。正式发布前的开发版本可能继续增加兼容修正。

## 0.1.0 - 2026-08-17

- 独立提供 `linkerbot_hand` 统一设备接口和 `linkerbot.hand` 兼容入口。
- 包含 L6、O6、L20Lite、L25、L30、O20 和 O30i 驱动与能力描述。
- 使用独立 `linkerbot-transport` 包提供 CAN/CAN FD、错误和并发原语。
- 保留统一百分比、协议原始值、非阻塞调度、缓存快照、健康状态和安全恢复契约。
- 角度映射配置遵循 `XDG_CONFIG_HOME`，未设置时继续使用 `~/.config`。
- 默认 README 提供完整中文说明，并保留独立英文版本。
