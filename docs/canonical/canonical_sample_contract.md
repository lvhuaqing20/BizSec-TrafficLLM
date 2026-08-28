# CanonicalTrafficSample 契约 v1

状态：阶段3已实现并通过校验。

## 1. 定位

`CanonicalTrafficSample` 是原始数据集解析器与任务 View Builder 之间的统一中间层：

```text
TrafficLLM JSONL / 未来实时流量
        ↓ 解析、标准化、隐私处理
CanonicalTrafficSample
        ↓ 按任务选择一种主要表达并移除标签区
Business / Detection / Attack-Type View
```

它不是模型 Prompt，也不是 Adapter 输入；View Builder 不得把 `source`、`labels` 或完整原始 `instruction` 复制到任务 View。

## 2. 已解析事实与设计决定

### 已解析事实

- TrafficLLM_Datasets 包含 11 个数据集变体；
- train/test 共 544,381 条可解析 JSONL 记录；
- 每条原始记录的顶层字段为 `instruction` 和 `output`；
- 流量正文嵌在 `instruction`，实际观察到 TShark 包文本、HTTP request JSON、二进制方向序列三种结构；
- 原始数据集通常只提供一个任务维度的标签。

### 阶段3设计决定

- 统一样本保存解析后的结构化流量，不保存完整原始 Prompt；
- `traffic` 与 `labels` 严格分区；
- 一个统一样本可以保存多种可复现表达，但必须指定一个 `primary_representation`；
- 解析失败或配置/内容冲突的记录进入转换失败报告，不生成伪造的统一样本；
- 精确 IP、MAC、绝对路径和未处理的直接标识符不进入统一样本。

## 3. 顶层结构

```json
{
  "canonical_version": "canonical-traffic-sample-v1",
  "sample_id": "<64-char sha256>",
  "source": {},
  "traffic": {},
  "context": {},
  "labels": {},
  "quality": {}
}
```

推理输入没有真值时，`labels` 必须为 `null`。训练、验证和测试数据的 `labels` 保存原标签、归一化标签和可训练目标。

## 4. 稳定样本 ID

数据集样本 ID 的唯一算法为：

```text
sha256(dataset_id + NUL + split + NUL + source_file + NUL + record_index)
```

- `record_index` 从 0 开始；
- `source_file` 是相对于数据集根目录的 POSIX 路径；
- 原始行另存 `source_record_sha256`，用于检测源文件内容变化；
- 相同数据版本重复转换必须得到相同 `sample_id`。

实时样本同样使用 64 位小写 SHA-256 ID，但事件 ID 生成算法由后续实时接入契约定义。

## 5. 来源追踪

### 数据集样本

- `source_kind=dataset`；
- dataset、split、相对文件、0 起始记录序号和原始记录哈希均必填；
- 禁止绝对路径和 `..` 路径穿越；
- `source_format` 必须与数据集映射配置一致。

### 实时样本

- `source_kind=live`；
- `split=inference`；
- dataset、source file、record index 和 source record hash 均为 null；
- `source_format=live_structured`。

## 6. 流量表达

`traffic.representations` 固定包含三个槽位，不可用的表示为 null：

| 槽位 | 内容 |
|---|---|
| `packet` | 协议、方向、包长、网络角色、传输层、TLS、受控 payload |
| `http_request` | method、host、path、query、body |
| `direction_sequence` | `0/1` 二进制方向序列 |

约束：

- 至少有一个非 null 表示；
- `primary_representation` 必须指向一个非 null 表示；
- `quality.available_representations` 必须与非 null 槽位完全一致；
- 数据集样本的主表示必须与来源映射及实际内容一致；
- 统计特征独立存放，不拼接进表达正文。

## 7. 标签隔离

`labels` 包含：

- `raw.value`：原始 output；
- `raw.normalized_value`：阶段1规则归一化后的标签；
- `eligible_tasks`：该样本具备监督真值的任务；
- `targets`：Business、Detection、Attack-Type 三个目标，缺失真值使用 null；
- `mapping`：标签注册表版本、映射依据和人工复核状态。

跨字段约束：

- `eligible_tasks` 必须等于所有非 null target 的键集合；
- Attack-Type target 必须同时具有 `is_attack=true` 的 Detection target；
- `is_attack=false` 时 Attack-Type target 必须为 null；
- `benign` 不是 Attack-Type Adapter 标签。

## 8. 质量与隐私

- `parse_status=ok`：满足本表示全部必要字段；
- `parse_status=partial`：仍能生成有效样本，但必须列出缺失字段；
- `available_representations`：实际成功构造的表达；
- `warnings`：不阻断转换的异常；
- `privacy.contains_direct_identifiers` 固定为 false；
- 实施过隐私处理时，必须在 `privacy.transforms` 中记录转换名称。

无法满足任何一个有效表示、格式冲突或隐私处理失败时，不得把状态降级为 partial 来勉强通过，应进入转换失败报告。

## 9. 与 View Engine 的边界

View Builder 只能读取：

- `sample_id`；
- 被任务允许的一种流量表示；
- 可用统计字段；
- `context`；
- `quality` 的受控子集。

训练目标由训练样本构造器单独读取 `labels.targets.<task>`；`labels` 与 `source` 不进入模型输入。推理时 `labels=null`，避免把训练数据特有字段带入线上路径。

## 10. 当前具体实现

阶段3由四层组成：

1. **Schema 层**：`canonical_traffic_sample.schema.json` 定义统一顶层结构、来源、三种流量表达、上下文、标签和质量信息；通过 `additionalProperties: false` 禁止未声明字段。
2. **配置层**：`source_mapping_v1.json` 配置 11 个数据集对应的来源格式、Parser ID 和预期主表达；`representation_detection_v1.json` 配置实际内容检测、冲突策略和任务 View 许可。
3. **校验层**：`validate_canonical_contracts.py` 同时执行 JSON Schema 校验和跨字段语义校验，包括可重算 sample ID、相对路径、表达一致性、标签逻辑、隐私状态以及阶段1/2配置一致性。
4. **测试层**：使用 4 个合法样例和 7 个确定性非法变异测试正确接受与拒绝行为，并通过 `test_canonical_contracts.py` 接入项目回归测试。

当前已实现：

```text
统一样本结构
+ 11 个数据集来源映射
+ 3 种表达识别规则
+ 标签隔离与来源追踪规则
+ 自动校验与回归测试
```

当前尚未实现：

```text
原始 instruction
    ↓ 真实 Parser
CanonicalTrafficSample JSONL
    ↓ 批量转换与失败审计
完整统一数据集
```

因此，阶段3实现的是“统一数据层的契约和质量门禁”，不是已经完成全量数据转换。真实 Parser、Canonical Sample 构造器和 544,381 条记录的转换属于阶段4。

## 11. 机器契约

- Schema：`schemas/canonical/canonical_traffic_sample.schema.json`
- 来源映射：`configs/canonical/source_mapping_v1.json`
- 表达识别：`configs/canonical/representation_detection_v1.json`
- 校验器：`scripts/validate_canonical_contracts.py`
