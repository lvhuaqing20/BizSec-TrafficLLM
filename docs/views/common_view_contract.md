# 公共 View 契约 v1

## 1. 定位

View 是 Adapter 的结构化输入，不是原始流量、训练目标或自然语言 Prompt。后续 Serializer 负责将 View 放入具体基础模型的 Chat Template。

## 2. 公共外壳

三个 View 均包含：

```json
{
  "view_version": "...",
  "task": "...",
  "sample_id": "...",
  "granularity": "packet",
  "traffic": {},
  "context": {},
  "priors": {},
  "quality": {}
}
```

`sample_id` 是稳定哈希或非敏感内部 ID，不能直接使用完整五元组、IP 或原始文件路径。

## 3. 支持的流量表示

### packet

包含协议栈、方向、包长、网络角色、传输层、TLS 和受控 payload 表示。精确 IP/MAC 不进入 View。

### http_request

包含 method、host、path、query 和 body。敏感值在上游统一样本阶段脱敏，在 Token 管理阶段确定性截断。

### direction_sequence

使用 `0/1` 二进制方向序列，仅用于 Business v1 网站指纹任务。

## 4. 表达类型的确定规则

流量表达不是由 View Builder 任意选择，也不能只根据数据集名称写死。它由两个条件共同决定：

1. **原始样本实际结构**决定能够构造哪些表达；
2. **目标任务的 View Schema**决定该 Adapter 允许接收哪些表达。

只有同时满足这两个条件，才生成对应任务 View：

```text
原始样本结构
    ↓ 解析与识别
可用 representation
    ↓ 任务许可检查
目标任务 View
```

当前 TrafficLLM 数据的识别规则如下：

| 原始样本特征 | 识别结果 |
|---|---|
| 包含 method、URL/path、query、body 等 HTTP 字段 | `http_request` |
| 正文为纯 `0/1` 双向序列 | `direction_sequence` |
| 包含 frame、IP、TCP/UDP、TLS、payload 等包级字段 | `packet` |

数据集配置可以声明预期格式，用于选择解析器和审计；Adapter 仍须检查样本实际内容。声明与内容不一致时记录解析/转换错误，不得强制转换成另一种表示。

当前任务许可为：

| representation_type | Business | Detection | Attack-Type |
|---|---:|---:|---:|
| `packet` | 允许 | 允许 | 允许 |
| `http_request` | 允许 | 允许 | 允许 |
| `direction_sequence` | 允许 | 不允许 | 不允许 |

后续处理原始 PCAP 时，一个统一样本可以同时保存多种可复现表示；单个任务 View 只选择一种主要表示，统计特征独立存放，避免把不同粒度的数据混成一个正文。

## 5. 缺失语义

- 字段存在但值未知：使用 `null`；
- 整个可选对象不可用：使用 `null`；
- 必要字段缺失：记录在 `quality.missing_fields`；
- 仍可构造有效 View：`parse_status=partial`；
- 无法满足 P0 字段：不生成 View，进入转换失败报告。

## 6. Prior 规则

- Business View 不接受 Prior；
- Detection/Attack-Type 只接受 Business Adapter 的结构化输出或 null；
- v1 不接受 confidence；
- Attack-Type 不重复注入 Detection 的 `is_attack=true`。

## 7. 表示与粒度一致性

| representation_type | granularity | quality.source_representation |
|---|---|---|
| packet | packet | packet |
| http_request | request | http_request |
| direction_sequence | direction_sequence | direction_sequence |

后续 flow/window 扩展必须升级 View Schema 后才能使用。

## 8. 禁止内容

- 原始 `instruction` 和模型 Prompt；
- 原始或标准标签；
- ground truth、target、candidate labels；
- confidence、evidence_codes、decision_source；
- 精确 IP、MAC、绝对文件路径；
- 数据集名称和 split 信息。
