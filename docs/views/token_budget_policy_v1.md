# View Token 预算策略 v1

机器可读配置：`configs/views/token_budget_v1.json`。

## 原则

1. 先执行字符级安全上限，再使用部署模型的统一 tokenizer 计算真实 Token；
2. 为结构化输出保留 64 个 Token；
3. 按 P3 → P2 → P1 顺序确定性裁剪；
4. P0 不删除；
5. 如果仅 P0 已超限，则拒绝构造，不使用字符串尾部硬截断。

## 任务差异

- Business 优先保留 SNI、Host、Path、目标端口和方向序列；
- Detection 优先保留 HTTP 核心字段、异常统计和 Business Prior；
- Attack-Type 优先保留 HTTP 攻击内容、payload 和规则代码。

## 可复现要求

相同 View、相同 tokenizer、相同部署配置必须产生相同裁剪结果。裁剪后的 View 在 `quality.warnings` 中记录受控代码，不写自然语言过程。
