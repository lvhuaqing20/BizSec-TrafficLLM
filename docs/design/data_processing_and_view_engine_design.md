# 数据处理与任务视图构造设计实现文档

文档版本：v1.0  
对应实现：Canonical v1、View Engine v1、Messages v1  
当前状态：已完成全量数据转换、视图生成和独立校验

## 1. 文档目的

本文统一说明BizSec-TrafficLLM项目中从原始TrafficLLM数据到模型训练输入之间的设计、实现、数据边界和验证结果，供以下场景使用：

- 项目设计评审和阶段汇报；
- 本地数据工程与服务器训练工程交接；
- 新数据源、新任务或新流量表达的扩展；
- 数据泄漏、隐私、可重复性和训练异常排查。

本文覆盖：

```text
TrafficLLM_Datasets
  → 标签审计和任务映射
  → 原始流量解析
  → CanonicalTrafficSample
  → Representation Selector
  → Business / Detection / Attack-Type View
  → TaskTrainingExample
  → Messages Dataset
  → ChatGLM2训练输入适配边界
```

本文不覆盖GPU训练、Prefix checkpoint管理和模型推理实现；这些属于训练与推理层。

## 2. 核心设计原则

### 2.1 统一数据源

所有任务View只读取`CanonicalTrafficSample`，不直接解析原始`instruction`。原始数据格式差异只由数据处理层处理，View Engine不感知具体数据集文件结构。

### 2.2 流量证据与标签隔离

流量证据保存在`traffic/context/quality`，监督标签保存在`labels`。View Engine不能读取标签构造模型输入；训练目标由`TrainingViewGenerator`单独取出并放在View外部。

### 2.3 表达可用性与任务许可同时生效

三种流量表达由原始样本实际结构决定，任务是否能使用该表达由View策略决定。不能仅依据数据集名称强制构造表达，也不能把HTTP、Packet和方向序列混成同一段文本。

### 2.4 确定性和可审计性

相同原始文件、配置和代码必须生成相同sample ID、字段顺序、裁剪结果和文件哈希。每一层都保留版本号、错误码、数量统计和独立校验报告。

### 2.5 最小化敏感信息

精确IP转换为网络角色，MAC和绝对时间删除，HTTP敏感值确定性脱敏，payload受控截断或摘要化。失败记录不得复制完整原始流量。

### 2.6 不生成伪标签

只有原数据明确提供任务语义时才生成对应训练样本。`null`表示没有监督，不表示benign、unknown class或推理失败。

## 3. 总体架构

```text
┌──────────────────────────────┐
│ TrafficLLM原始JSONL          │
│ instruction + output         │
└──────────────┬───────────────┘
               │ 只读、逐行
               ▼
┌──────────────────────────────┐
│ Parser Router                │
│ 来源配置路由 + 实际内容检查  │
└───────┬──────────┬───────────┘
        │          │
        ▼          ▼
 TShark Packet   CSIC HTTP   Direction Sequence
        └──────────┬───────────┘
                   ▼
┌──────────────────────────────┐
│ LabelResolver                │
│ 标签归一化 + 任务target查询  │
└──────────────┬───────────────┘
               ▼
┌──────────────────────────────┐
│ CanonicalSampleBuilder       │
│ source/traffic/context/      │
│ labels/quality               │
└──────────────┬───────────────┘
               ▼
      Schema + 语义 + 隐私校验
               ▼
┌──────────────────────────────┐
│ CanonicalTrafficSample       │
└──────────────┬───────────────┘
               ▼
┌──────────────────────────────┐
│ View Engine                  │
│ 表达选择 → 预Token裁剪 →     │
│ 任务字段组装 → View校验      │
└───────┬──────────┬───────────┘
        ▼          ▼           ▼
   Business    Detection   Attack-Type
      View        View         View
        └──────────┬───────────┘
                   ▼
           view + 独立target
                   ▼
        TaskTrainingExample / Messages
```

## 4. 原始数据和任务映射

### 4.1 原始公共结构

TrafficLLM_Datasets包含11个数据集变体。实际原始记录是JSONL，每条记录的公共结构为：

```json
{
  "instruction": "任务说明和流量正文",
  "output": "原始标签"
}
```

全量train/test共544,381条记录，原始文件在整个转换过程中保持只读。

### 4.2 三种原始流量结构

| 原始结构 | 识别特征 | Canonical表达 | Parser |
|---|---|---|---|
| TShark包文本 | `frame.*`、IP、TCP/UDP、TLS、payload字段 | `packet` | `tshark_packet_text_v1` |
| HTTP请求JSON | method、URL、query、body | `http_request` | `csic_http_json_v1` |
| 二进制方向序列 | instruction末尾唯一`Input: 0101...` | `direction_sequence` | `binary_direction_sequence_v1` |

### 4.3 数据集到主要任务

| 数据集 | Business | Detection | Attack-Type | 主要表达 |
|---|---:|---:|---:|---|
| app53-2023 | 是 | 否 | 否 | packet |
| csic-2010 | 否 | 是 | 部分 | HTTP |
| cstnet-2023 | 是 | 否 | 否 | packet |
| cw100-2018 | 是 | 否 | 否 | direction sequence |
| cw100-2024 | 是 | 否 | 否 | packet |
| dapt-2020 | 否 | 是 | 部分 | packet |
| dohbrw-2020 | 否 | 是 | 部分 | packet |
| iscx-botnet-2014 | 否 | 是 | 部分 | packet |
| iscx-tor-2016 | 是 | 否 | 否 | packet |
| iscx-vpn-2016 | 是 | 否 | 否 | packet |
| ustc-tfc-2016 | 部分 | 是 | 部分 | packet |

应用、网站、Tor和VPN标签不能自动解释为安全或攻击标签。USTC中的正常应用进入Business和Detection，恶意家族进入Detection和Attack-Type；攻击家族不能被当成业务类别。

### 4.4 标签注册表

标签注册表以`dataset_id + normalized_label`为查询键，负责：

1. 保存原标签和归一化规则；
2. 声明样本可以训练哪些任务；
3. 生成Business、Detection、Attack-Type结构化target；
4. 记录映射依据和人工复核状态；
5. 拒绝未注册标签，避免静默生成错误监督。

当前注册表声明290个标签，实际数据观测到283个。CW100-2024有7个声明但未观测标签：`cn`、`com`、`edu`、`fr`、`gov`、`in`、`jp`。`iscx-botnet-2014/IRC`保留人工复核标记。

## 5. Canonical统一数据层

### 5.1 定位

`CanonicalTrafficSample`是原始Parser和View Engine之间的统一中间层，相当于三个任务共同使用的标准化数据源。它不是Prompt，也不是直接发送给模型的输入。

### 5.2 顶层结构

```json
{
  "canonical_version": "canonical-traffic-sample-v1",
  "sample_id": "64位SHA-256",
  "source": {},
  "traffic": {},
  "context": {},
  "labels": {},
  "quality": {}
}
```

| 区域 | 职责 | 是否进入View |
|---|---|---:|
| `source` | 数据集、split、相对文件、记录序号、源行哈希 | 否 |
| `traffic` | 三种标准化流量表达和统计字段 | 按任务选择 |
| `context` | 受控资产、服务或外部上下文 | 按任务白名单 |
| `labels` | 原标签、归一化标签、eligible tasks、targets | 绝不进入View |
| `quality` | 解析状态、缺失字段、warning、隐私转换 | 受控子集 |

推理输入没有真值时，`labels`必须为`null`。

### 5.3 稳定sample ID

数据集样本使用以下确定性算法：

```text
sha256(dataset_id + NUL + split + NUL + source_file + NUL + record_index)
```

同时记录原始行的`source_record_sha256`。这样可以区分“样本位置是否相同”和“源文件内容是否发生变化”。

### 5.4 Packet解析

Packet Parser提取：

- 协议栈和包长；
- IPv4/IPv6版本；
- internal/external/unknown网络角色；
- TCP/UDP端口和TCP flags；
- TLS SNI、ALPN和版本；
- 受控payload内容、原长度或摘要。

实现上的关键处理：

- 使用下一个合法TShark字段名作为字段边界，不按所有逗号直接切分；
- 精确IP、MAC、绝对时间不写入Canonical；
- 超长hex payload截断；非hex payload替换为SHA-256摘要；
- 嵌套ICMP/IP报文稳定选取最内层被引用流量，并记录warning。

### 5.5 HTTP解析

HTTP Parser从任务标记之后使用JSON decoder提取唯一请求对象，并用URL parser拆分path和query。

- method去除空白并转大写；
- path保留攻击结构；
- query/body对密码、账号、session、token、邮箱、证件和卡号等敏感值做确定性脱敏；
- 原数据缺失Host时保留`host=null`并标记`partial`；
- 不能用简单字符串切割破坏URL编码内容。

### 5.6 Direction Sequence解析

Direction Parser只读取instruction末尾锚定的`Input:`或`Input：`后的连续`0/1`串。任务示例、候选网站列表和说明文本都不能进入序列。

### 5.7 质量状态

- `ok`：当前表达要求的必要字段完整；
- `partial`：仍能构造有效样本，但存在明确缺失字段；
- 转换失败：没有任何有效表达、内容和配置冲突、标签未知或隐私处理失败。

`partial`不能用来掩盖无法满足必要字段的错误。

### 5.8 成功、失败和报告输出

```text
artifacts/datasets/canonical/v1/
├── canonical/<dataset>/<split>.jsonl
├── failures/<dataset>/<split>.jsonl
└── conversion_report.json
```

失败文件只包含最小定位字段、源行哈希、错误码和截断错误信息，不包含完整instruction或payload。单条记录失败不会中止整个批次，配置错误或文件缺失则立即失败。

## 6. View Engine设计

### 6.1 View的作用

View是某一个Adapter可见的结构化流量证据。它解决以下问题：

- 三个任务需要的字段不同；
- 某些流量表达只适合特定任务；
- 原始标签和数据集信息不能进入模型输入；
- 长字段需要按照任务确定性裁剪；
- 推理时需要安全地注入上游Business结果。

### 6.2 公共View外壳

```json
{
  "view_version": "business-view-v1",
  "task": "business_classification",
  "sample_id": "...",
  "granularity": "packet",
  "traffic": {
    "representation": {},
    "statistics": null
  },
  "context": {},
  "priors": {},
  "quality": {
    "parse_status": "ok",
    "source_representation": "packet",
    "missing_fields": [],
    "warnings": []
  }
}
```

View中不包含dataset、split、source path、原始instruction、标签或候选类别。

### 6.3 表达选择算法

`RepresentationSelector`执行：

```text
读取Canonical可用表达
        ↓
主表达是否被当前任务允许？
  ├── 是 → 选择主表达
  └── 否 → 按任务fallback顺序选择
                 ↓
          仍无可用表达 → view_unavailable
```

当前许可矩阵：

| 表达 | Business | Detection | Attack-Type |
|---|---:|---:|---:|
| packet | 允许 | 允许 | 允许 |
| http_request | 允许 | 允许 | 允许 |
| direction_sequence | 允许 | 禁止 | 禁止 |

使用非主表达时写入`view_used_non_primary_representation` warning。

### 6.4 Business View

任务目标：识别业务域和业务类型。

主要字段：

- 协议栈、目标端口、方向和包长；
- TLS SNI/ALPN；
- HTTP Host/Path；
- direction sequence；
- asset type和service name。

Business View禁止Business prior和安全标签，其`priors`固定为空对象。

训练target示例：

```json
{"business_domain":"application","business_type":"spotify"}
```

### 6.5 Detection View

任务目标：判断`is_attack`。

允许packet和HTTP表达；可以包含受控安全上下文：

- `rule_hits`：稳定规则代码列表；
- `threat_intel_hit`：true、false或null；
- `priors.business`：Business Adapter结构化输出或null。

禁止将Detection target、Attack-Type真值、原始output、候选类别或confidence放入View。

训练target：

```json
{"is_attack":true}
```

### 6.6 Attack-Type View

任务目标：流水线已经判定为攻击后，识别攻击大类和攻击家族。

允许packet和HTTP表达；可以使用Business prior，但不注入Detection的`is_attack=true`。调用Attack-Type本身已经表达了门控结果，再注入检测答案会形成冗余提示。

训练target示例：

```json
{"attack_type":"botnet","attack_family":"Neris"}
```

`benign`不是Attack-Type类别。Detection为false时，由编排器直接生成：

```json
{"attack_type":"benign","attack_family":null}
```

### 6.7 Business Prior边界

推理阶段：

```text
Business View → Business模型输出
                      ↓
      注入Detection/Attack-Type View的priors.business
```

训练阶段当前采用独立任务基线，Detection和Attack-Type的Business prior为`null`。不能直接使用Business ground truth伪造上游模型输出，否则训练数据会看不到真实Business模型的错误分布。

最终级联增强版应先训练Business模型，再使用out-of-fold Business预测生成prior增强训练数据。

### 6.8 预Tokenizer预算

当前View Engine先执行字符级安全上限：

| 任务 | payload | HTTP query | HTTP body | direction sequence |
|---|---:|---:|---:|---:|
| Business | 256 | 512 | 512 | 4096 |
| Detection | 2048 | 2048 | 4096 | 不适用 |
| Attack-Type | 4096 | 2048 | 4096 | 不适用 |

裁剪规则：

1. 深拷贝表达，不能修改Canonical源对象；
2. 相同输入始终保留相同前缀；
3. 发生裁剪时写入稳定warning；
4. P0必要字段不能删除；
5. 真实ChatGLM2 token预算属于下一层，字符上限不等于最终token上限。

### 6.9 泄漏防护

以下来源或字段禁止进入任何View：

- `labels`、`target`、`ground_truth`、原始`output`；
- 原始`instruction`、模型Prompt和候选类别；
- dataset、split、绝对文件路径；
- confidence、decision source和evidence codes；
- 精确IP、MAC和其他直接标识符。

泄漏检查关注字段来源、禁止键和Prompt片段，不会简单删除真实流量中自然出现的业务名称。例如真实SNI中的`gmail.com`可以保留，但不能把`output=Gmail`复制成SNI。

## 7. 训练样本和Messages构造

### 7.1 TaskTrainingExample

`TrainingViewGenerator`只为存在对应target的样本生成训练实例：

```json
{
  "example_version": "task-training-example-v1",
  "sample_id": "...",
  "task": "detection",
  "view": {},
  "target": {"is_attack": true}
}
```

一个Canonical样本可能具有多个任务target，因此可以生成多个`sample/task`实例。去重键是`sample_id + task`，不是单独的`sample_id`。

### 7.2 Messages Dataset

Serializer将训练实例确定性转换为：

```text
system     固定任务指令
user       紧凑、键排序的JSON View
assistant  紧凑、键排序的JSON target
```

推理消息只有system和user，不包含assistant。训练和推理共享相同Prompt配置。

Messages层不添加基础模型特殊token。当前ChatGLM2适配层读取Messages后，将system和user组成query，assistant作为response，并只对response计算loss。

## 8. 代码和配置结构

### 8.1 数据处理实现

| 路径 | 职责 |
|---|---|
| `src/bizsec_trafficllm/data/parser_base.py` | Parser接口 |
| `src/bizsec_trafficllm/data/parser_router.py` | 来源路由和表达一致性检查 |
| `src/bizsec_trafficllm/data/tshark_packet_parser.py` | Packet解析 |
| `src/bizsec_trafficllm/data/csic_http_parser.py` | HTTP解析和脱敏 |
| `src/bizsec_trafficllm/data/direction_sequence_parser.py` | 方向序列解析 |
| `src/bizsec_trafficllm/data/label_resolver.py` | 标签归一化和target查询 |
| `src/bizsec_trafficllm/data/canonical_builder.py` | Canonical组装和sample ID |
| `src/bizsec_trafficllm/data/canonical_validation.py` | Schema和跨字段校验 |
| `src/bizsec_trafficllm/data/conversion.py` | 流式转换、失败分流和统计 |

### 8.2 View实现

| 路径 | 职责 |
|---|---|
| `src/bizsec_trafficllm/views/selector.py` | 任务表达选择 |
| `src/bizsec_trafficllm/views/budget.py` | 字符级预Token裁剪 |
| `src/bizsec_trafficllm/views/builder.py` | 三任务View组装 |
| `src/bizsec_trafficllm/views/validation.py` | View Schema校验 |
| `src/bizsec_trafficllm/views/training.py` | View和target分离的训练实例 |

### 8.3 Serializer实现

| 路径 | 职责 |
|---|---|
| `src/bizsec_trafficllm/serialization/canonical_json.py` | 确定性紧凑JSON |
| `src/bizsec_trafficllm/serialization/templates.py` | 三任务Prompt配置 |
| `src/bizsec_trafficllm/serialization/serializer.py` | 训练/推理Messages构造 |
| `src/bizsec_trafficllm/serialization/validation.py` | View、target和消息校验 |

### 8.4 核心机器契约

```text
configs/labels/label_registry_v1.json
configs/canonical/source_mapping_v1.json
configs/canonical/representation_detection_v1.json
configs/canonical/privacy_policy_v1.json
configs/views/representation_selection_v1.json
configs/views/field_registry_v1.json
configs/views/leakage_policy_v1.json
configs/views/token_budget_v1.json
configs/serialization/prompt_templates_v1.json

schemas/canonical/canonical_traffic_sample.schema.json
schemas/views/business_view.schema.json
schemas/views/detection_view.schema.json
schemas/views/attack_type_view.schema.json
schemas/training/task_example.schema.json
schemas/serialization/training_message_example.schema.json
schemas/serialization/inference_message_request.schema.json
```

## 9. 执行方式

### 9.1 全量Canonical转换

```bash
python scripts/convert_trafficllm_dataset.py \
  --data-root ../TrafficLLM_Datasets \
  --dataset all \
  --split all \
  --output-dir artifacts/datasets/canonical/v1
```

### 9.2 Canonical独立校验

```bash
python scripts/validate_converted_samples.py \
  --input-dir artifacts/datasets/canonical/v1 \
  --schema-root schemas \
  --expected-records 544381 \
  --report reports/phase4/full_validation_v1.json
```

### 9.3 生成三任务View

```bash
python scripts/build_task_views.py \
  --canonical-dir artifacts/datasets/canonical/v1/canonical \
  --output-dir artifacts/datasets/task_views/v1 \
  --schema-root schemas \
  --config-root configs/views \
  --task all
```

### 9.4 View独立校验

```bash
python scripts/validate_task_views.py \
  --input-dir artifacts/datasets/task_views/v1 \
  --schema-root schemas \
  --expected-business 390279 \
  --expected-detection 170423 \
  --expected-attack-type 100462 \
  --report reports/phase5/full_views_validation_v1.json
```

### 9.5 生成和校验Messages

```bash
python scripts/build_training_messages.py \
  --view-dir artifacts/datasets/task_views/v1 \
  --output-dir artifacts/datasets/messages/v1 \
  --schema-root schemas \
  --prompt-config configs/serialization/prompt_templates_v1.json \
  --task all

python scripts/validate_training_messages.py \
  --input-dir artifacts/datasets/messages/v1 \
  --source-view-dir artifacts/datasets/task_views/v1 \
  --schema-root schemas \
  --prompt-config configs/serialization/prompt_templates_v1.json \
  --expected-business 390279 \
  --expected-detection 170423 \
  --expected-attack-type 100462 \
  --report reports/phase6/full_messages_validation_v1.json
```

## 10. 全量结果和验证证据

### 10.1 Canonical数据

| 指标 | 结果 |
|---|---:|
| 原始/成功记录 | 544,381 |
| 失败记录 | 0 |
| 唯一sample ID | 544,381 |
| ok | 453,072 |
| partial | 91,309 |
| packet | 502,377 |
| HTTP | 34,604 |
| direction sequence | 7,400 |

Canonical组合SHA-256：

```text
09b97848279ca2f25873f97b5531ff67716b8f44ae146a44ee9f25636fddf2da
```

### 10.2 Task View数据

| 任务 | 数量 |
|---|---:|
| Business | 390,279 |
| Detection | 170,423 |
| Attack-Type | 100,462 |
| 合计sample/task实例 | 661,164 |
| 重复sample/task | 0 |

Task View组合SHA-256：

```text
93af8d43eeeef338ea7a9ebcd05ab2cd9917b4c7c086759baa13c9f71d91362e
```

### 10.3 Messages数据

| 划分 | 数量 |
|---|---:|
| train | 618,152 |
| test | 43,012 |
| 总计 | 661,164 |

Messages组合SHA-256：

```text
d8ec9e8e85d288c149f07d9764cfdb2a6a17cde93c6634ed9c0c2134fea8e17a
```

独立校验已经覆盖：

- Draft 2020-12 Schema；
- 跨字段标签逻辑；
- sample ID重算和唯一性；
- 输入/输出记录守恒；
- 隐私字段和敏感信息；
- View/target隔离；
- 消息与源View逐行确定性一致；
- train/test路径保持；
- 组合内容哈希。

## 11. 训练与推理中的数据路径

### 11.1 当前独立训练基线

```text
Canonical(labels存在)
   ↓
按eligible task生成View，Business prior=null
   ↓
view + target
   ↓
Messages
   ↓
ChatGLM2 query/response
```

### 11.2 正式级联推理

```text
单个原始/实时流量输入
   ↓ Parser + Canonical Builder
CanonicalTrafficSample(labels=null)
   ↓
Business View → Business Prefix
   ↓ business output
Detection View + Business Prior → Detection Prefix
   ↓
is_attack=false → 编排器输出benign
is_attack=true  → Attack-Type View + Business Prior → Attack-Type Prefix
```

### 11.3 最终级联训练增强

```text
训练Business Prefix
   ↓
对Detection/Attack-Type训练样本生成out-of-fold Business预测
   ↓
将预测作为Business prior重建View和Messages
   ↓
训练Detection和Attack-Type Prefix
```

必须使用预测先验而不是业务真值，才能让下游模型学习上游误差。

## 12. 当前边界和后续扩展

### 12.1 已完成

- 11个数据集和完整标签注册表；
- 三种真实Parser；
- 544,381条Canonical全量转换；
- 三任务View Engine；
- 661,164条Task View和Messages；
- Schema、语义、隐私、唯一性和确定性校验；
- ChatGLM2 Messages输入适配和answer-only label mask算法。

### 12.2 尚未完成

- 服务器真实ChatGLM2 tokenizer全量长度审计；
- 根据真实token统计回填最终长度上限；
- P-Tuning v2统一训练入口和GPU训练；
- out-of-fold Business prior增强数据；
- 实时PCAP/flow/window输入契约；
- 多任务完整真值数据集上的端到端级联评估。

### 12.3 扩展约束

新增数据集时必须依次增加：

1. 数据源映射和Parser路由；
2. 标签注册表及任务资格；
3. Parser fixture和隐私规则；
4. Canonical转换与失败审计；
5. View表达许可和字段策略；
6. Schema/语义/泄漏回归测试；
7. 全量数量和哈希报告。

新增flow、session或window表达时，必须升级Canonical和View Schema版本，不能把不同粒度字段直接塞入现有packet结构。

## 13. 关键设计结论

1. `CanonicalTrafficSample`是统一数据源，View是任务专用输入，两者不能合并为一个层次。
2. 三种流量表达首先由原数据实际结构决定，再由任务策略决定是否可用。
3. 一个Canonical样本可以生成多个任务实例，661,164大于544,381是任务展开结果。
4. View中不包含标签；target只在训练样本外层和assistant消息中出现。
5. 当前Detection和Attack-Type训练prior为null，属于独立基线，不是最终级联增强版。
6. 字符预算只负责前置安全裁剪，最终token长度必须由ChatGLM2原生Tokenizer审计确定。
7. 全量数据结论来自独立验证报告和内容哈希，不由pilot样本外推。
