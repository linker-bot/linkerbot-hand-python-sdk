# 速度控制

通过 `hand.speed` 设置和读取 L30 的 17 个关节速度。

- **数量**：17 个关节，顺序为 `J1` 到 `J17`
- **命令范围**：`1..250` 协议整数
- **反馈编码**：有符号大端 `i16_be` 协议值
- **物理比例**：`0.732 rpm/count`

`set_speeds()`、`get_blocking()` 和统一接口都保留协议值，不执行百分比或 RPM 换算。
L30 未声明百分比速度能力。

统一 `SensorSample` 标注 `unit="count"`、`encoding="i16_be"`、
`physical_unit="rpm"`、`scale=0.732`、`offset=0`。默认 `values` 不自动换算为 RPM。

## 设置每个关节速度

```python
from linkerbot.hand import L30

with L30() as hand:
    hand.speed.set_speeds([50] * 17)
```

## 设置所有关节相同速度

```python
from linkerbot.hand import L30

with L30() as hand:
    hand.speed.set_all(50)
```

## 阻塞读取速度

```python
from linkerbot.hand import L30

with L30() as hand:
    data = hand.speed.get_blocking(timeout_ms=1000)
    print(data.speeds)
```

## 缓存读取

```python
from linkerbot.hand import L30

with L30() as hand:
    hand.speed.get_blocking(timeout_ms=1000)
    data = hand.speed.get_snapshot()
    if data is not None:
        print(data.speeds)
```

## 完整示例

```python
from linkerbot.hand import L30

with L30() as hand:
    hand.speed.set_all(50)
    data = hand.speed.get_blocking(timeout_ms=1000)
    print("当前速度：", data.speeds)
```
