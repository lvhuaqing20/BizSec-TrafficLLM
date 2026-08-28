# Detection View v1

## 目标

根据 packet 或 HTTP request 证据判断 `is_attack`。

## 支持表示

- packet：DAPT、DoH、Botnet、USTC 等；
- http_request：CSIC。

方向序列网站指纹数据没有安全标签，不进入 Detection v1。

## Business Prior

可用时：

```json
{
  "business": {
    "business_domain": "application",
    "business_type": "web_service"
  }
}
```

不可用时：

```json
{"business": null}
```

Prior 只能来自 Business Adapter 实际输出或明确的训练策略，不能直接注入该样本的 Business ground truth 而不记录来源。

## 安全上下文

允许受控规则代码和 threat-intel 布尔命中；禁止自然语言规则描述中携带真实标签。

## 禁止信息

- Detection 训练目标；
- Attack-Type 真实标签；
- 原数据 output；
- 候选类别列表；
- confidence 和 evidence_codes。
