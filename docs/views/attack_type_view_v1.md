# Attack-Type View v1

## 目标

在流水线已经判定 `is_attack=true` 后，识别攻击大类和可用的攻击家族。

## 支持表示

- packet：APT、恶意 DoH、Botnet、Malware；
- http_request：Web Attack。

## Prior

- 可以注入 Business Prior 或 null；
- 不注入 Detection Prior，因为 v1 只有 `is_attack=true`，调用本任务本身已经表达该条件。

## 高优先级字段

- HTTP method/path/query/body；
- payload 内容和长度；
- TCP flags；
- 规则代码；
- 攻击行为统计字段。

## 禁止信息

- attack_type/attack_family ground truth；
- 原始标签；
- 数据集类别列表；
- decision_source；
- benign 类别。
