# BizSec-TrafficLLM 任务契约 v1

状态：Draft，等待阶段1确认。

## 1. 通用规则

1. 三个 Adapter 均接收对应任务 View，不直接读取原始 TrafficLLM `instruction`。
2. 第一版不输出或使用模型置信度，只使用明确的结构化分类结果。
3. 所有输出使用结构化 JSON；禁止把自然语言解释作为唯一判定结果。
4. 不适用于某任务的样本不进入该任务训练集，不生成伪标签。
5. `null` 表示原数据没有对应监督，不等于 benign、unknown class 或预测失败。
6. 每个 Adapter 的训练目标和推理输出使用同一个 JSON Schema。

## 2. Business Adapter

### 2.1 任务目标

根据可观测流量特征识别业务域和业务类型。

### 2.2 训练目标与推理输出

```json
{
  "business_domain": "application",
  "business_type": "spotify"
}
```

`business_domain` v1 枚举：

- `application`：应用、软件或应用协议；
- `website`：网站指纹；
- `network_behavior`：Tor 等网络行为类型；
- `unknown`：模型无法可靠识别，仅用于推理输出，不作为当前数据集监督标签。

`business_type` 保存经格式清洗后的原始类别语义。v1 不强行合并 `facebook`、`com.facebook.katana` 等不同数据集分类体系。

## 3. Detection Adapter

### 3.1 任务目标

判断输入流量是否属于攻击或恶意通信。

### 3.2 训练目标与推理输出

```json
{
  "is_attack": true
}
```

只有原数据明确提供安全语义的样本才能进入 Detection 训练集。应用分类、网站分类、Tor 和 VPN 行为分类数据不自动视为 benign。

## 4. Attack-Type Adapter

### 4.1 调用条件

仅当 Detection 结果为 `is_attack=true` 时调用。

### 4.2 训练目标与推理输出

```json
{
  "attack_type": "malware",
  "attack_family": "Zeus"
}
```

`attack_type` v1 枚举：

- `web_attack`
- `apt`
- `malicious_doh`
- `botnet`
- `malware`
- `unknown_attack`

`attack_family` 仅在原数据提供细分类别时填写，否则为 `null`。

当 Detection 判定为非攻击时，不调用 Attack-Type Adapter。`benign` 属于流水线最终结果，不属于 Attack-Type Adapter 的输出类别。

编排器直接生成：

```json
{
  "attack_type": "benign",
  "attack_family": null
}
```

## 5. 训练与推理边界

| 字段 | Adapter训练目标 | Adapter推理输出 | 来源 |
|---|---|---|---|
| business_domain | 是 | 是 | 数据集标签映射 |
| business_type | 是 | 是 | 数据集标签映射 |
| is_attack | 是 | 是 | 数据集安全标签映射 |
| attack_type | 是，仅攻击样本 | 是 | 数据集语义映射 |
| attack_family | 有标注时 | 是或 null | 数据集细分类别 |

第一版不定义 `confidence`。如后续确有拒识、阈值控制或概率校准需求，再通过新契约版本增加，不能要求模型自由生成一个数值。

## 6. Schema 组织

三个 Adapter 的训练目标和推理输出分别共用：

- `schemas/adapters/business_output.schema.json`
- `schemas/adapters/detection_output.schema.json`
- `schemas/adapters/attack_type_output.schema.json`

完整级联结果单独使用：

- `schemas/pipeline/analysis_result.schema.json`

Adapter Schema 不包含 `benign` 门控结果、编排器元数据、风险融合结果或证据列表。这些字段属于流水线层。

## 7. 版本规则

- 当前契约版本：`task-contract-v1`。
- 标签语义、字段类型或枚举发生不兼容变化时升级主版本。
- 只增加可选字段时升级次版本。
- 训练数据 manifest 必须记录使用的契约版本。
