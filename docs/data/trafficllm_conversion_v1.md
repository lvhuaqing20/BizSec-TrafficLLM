# TrafficLLM 到 CanonicalTrafficSample 转换实现 v1

## 1. 数据链路

```text
原始 JSONL Reader
        ↓
Parser Router（依据来源配置选择，依据实际内容验证）
        ↓
TShark / CSIC HTTP / Direction Parser
        ↓
LabelResolver（只查询阶段1标签注册表）
        ↓
CanonicalSampleBuilder
        ↓
Draft 2020-12 + 语义校验
        ↓
canonical JSONL / failure JSONL / conversion report
```

原始 `TrafficLLM_Datasets` 只读。转换器不修改源文件，所有输出写入显式指定的 `--output-dir`。

## 2. 公共组件

| 组件 | 职责 |
|---|---|
| `parser_base.py` | 定义 Parser 接口 |
| `models.py` | 定义 `ParsedTraffic` 中间结果 |
| `errors.py` | 定义稳定的记录级 `ConversionError` 错误码 |
| `parser_router.py` | 根据 `parser_id` 路由并检查预期表达 |
| `label_resolver.py` | 执行阶段1标签归一化和 target 查询 |
| `canonical_builder.py` | 组合来源、流量、标签、质量和隐私记录 |
| `canonical_validation.py` | 运行时 Schema 与跨字段语义校验 |
| `conversion.py` | 流式读取、成功/失败分流、统计和文件哈希 |

## 3. 三个 Parser

### 3.1 TShark Packet

支持两种正文边界：

- 通用数据集的 `<packet>:`；
- iscx-vpn-2016 的说明换行后首个 `frame.*` 字段。

字段解析使用“下一个合法 TShark 字段名”作为边界，不按所有逗号直接切割，因此 `frame.time: Jul 21, 2020 ...` 不会被破坏。

标准化内容：

- `frame.protocols`、`frame.len`；
- IPv4/IPv6 版本；
- IP 根据受控 CIDR 转为 internal/external/unknown；
- TCP/UDP/ICMP、端口、TCP flags；
- TLS SNI/ALPN/version；
- payload 使用受控 hex 前缀，超限截断；非 hex 内容改为 SHA-256 summary。

精确 IP、MAC 和绝对时间不进入 Canonical Sample。

### 3.2 CSIC HTTP

从固定任务标记之后使用 JSON decoder 读取唯一 HTTP 对象，随后使用结构化 URL parser 拆分 host/path/query。

敏感参数名由 `privacy_policy_v1.json` 配置。密码、账号、Session、Token、邮箱、证件和卡号字段替换为确定性占位符；长数字和邮箱模式额外处理。攻击结构和非敏感参数尽量保留。

CSIC 原数据没有 Host，因此有效样本记录为 `partial`，并写入统一缺失字段路径。

### 3.3 Direction Sequence

只接受 instruction 末尾锚定的：

```text
Input: 0101...
Input: ：0101...
```

任务说明中的示例序列和网站候选列表不会被解析进统一样本。

## 4. 标签解析

`LabelResolver` 读取 `label_registry_v1.json`：

1. 按数据集执行已冻结的 strip/strip_suffixes/aliases；
2. 使用归一化标签精确查询注册表；
3. 复制 `eligible_tasks`、三个 targets、mapping basis 和 review 状态；
4. 未注册标签直接失败，不生成伪 target。

## 5. 成功与失败输出

成功记录：

```text
<output>/canonical/<dataset>/<split>.jsonl
```

失败记录：

```text
<output>/failures/<dataset>/<split>.jsonl
```

失败文件只包含 dataset、split、相对文件、record index、原始记录哈希、错误码和截断错误信息，不包含完整 instruction 或 payload。

汇总文件：

```text
<output>/conversion_report.json
```

包含成功、partial、失败、表达类型、错误码以及每个输出文件的 SHA-256。

## 6. 执行命令

调试时可以使用 `--limit N` 或按标签确定性分层抽样 `--sample-per-label N`；两者互斥。正式全量转换不指定两者：

```bash
python scripts/convert_trafficllm_dataset.py \
  --data-root ../TrafficLLM_Datasets \
  --dataset all \
  --split all \
  --output-dir artifacts/datasets/canonical/v1

python scripts/validate_converted_samples.py \
  --input-dir artifacts/datasets/canonical/v1 \
  --schema-root schemas \
  --expected-records 544381 \
  --report reports/phase4/full_validation_v1.json
```

## 7. Pilot与全量证据

阶段4 pilot 实际处理 11 个数据集的 train/test 前20条，共440条：

- 440条转换成功，0条失败；
- 360条 packet、40条 HTTP、40条 direction sequence；
- 360条 ok、80条 partial；
- 80条 partial 均有明确来源：40条CSIC缺Host，40条CW100-2024缺src/dst IP角色；
- 产物级检查未发现 instruction key、MAC或精确IPv4泄漏；
- 第二次转换组合哈希与首次完全一致。

Pilot只用于验证实现路径，随后已完成全量执行：

- 544,381条成功、0条失败、544,381个唯一sample ID；
- 453,072条ok、91,309条partial；
- 502,377条packet、34,604条HTTP、7,400条direction sequence；
- 首轮发现并修复177条嵌套ICMP/IP字段，稳定选择最内层被引用报文；
- 独立Schema、语义、隐私、记录守恒和唯一性检查全部通过；
- 全量组合SHA-256：`09b97848279ca2f25873f97b5531ff67716b8f44ae146a44ee9f25636fddf2da`。

因此全量结论来自 `reports/phase4/full_validation_v1.json`，不是由pilot外推。
