# linkerbot-hand API Reference

本目录是独立 `linkerbot-hand` 包的公开中文参考文档。

- [统一设备接口](./hand-device.md)
- [型号能力矩阵](./hand-capabilities.md)
- [兼容策略](./hand-compatibility.md)
- [统一接口迁移说明](./hand-migration.md)
- [CAN 总线](./can.md)
- [CAN FD 总线](./canfd.md)
- [L6](./l6/README.md)
- [O6](./o6/README.md)
- [L20Lite](./l20lite/README.md)
- [L25](./l25/README.md)
- [L30](./l30/README.md)
- [O20](./o20/README.md)
- [O30i](./o30i/README.md)

新应用使用 `linkerbot_hand` 统一接口；需要继续调用旧型号类时使用兼容路径
`linkerbot.hand`。两者由同一个 wheel 提供。
