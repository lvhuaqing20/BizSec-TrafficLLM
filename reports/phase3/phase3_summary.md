# 阶段3总结：CanonicalTrafficSample

## 一、阶段结论

阶段3已完成统一样本 Schema、11 个数据集来源映射、3 类表达识别策略、标签隔离、稳定 ID、质量/隐私契约、正反样例和自动校验器。

阶段3只冻结“原数据应转换成什么”，没有批量改写 TrafficLLM_Datasets。原始开源数据保持只读。

## 二、已解析证据

| 项目 | 结果 |
|---|---:|
| 数据集变体 | 11 |
| 已审计原始记录 | 544,381 |
| 原始记录结构 | JSONL `instruction` + `output` |
| 实际输入结构 | TShark、HTTP JSON、方向序列 |
| 已声明标签 | 290 |

这些事实来自阶段1完整审计和阶段3原文件样例复查，不等同于本阶段的设计假设。

## 三、关键设计

1. Canonical Sample 位于数据集解析器和 View Builder 之间，不是 Prompt。
2. `traffic` 与 `labels` 分区，View Builder 不读取 labels/source。
3. 统一样本可以保存多种表示，单任务 View 只选择一种主要表示。
4. 数据集配置只声明预期格式，实际内容冲突时转换失败，禁止强制转换。
5. 数据集样本 ID 可由 dataset、split、相对路径和 0 起始记录序号确定性重算。
6. 失败、歧义和隐私处理失败不伪装成 partial Canonical Sample。

## 四、当前实现边界

已实现的是统一数据层的四项基础设施：

```text
Canonical Schema
    + 数据集/表达配置
    + 跨字段校验器
    + 正反样例与回归测试
```

尚未实现：

- `tshark_packet_text_v1`、`csic_http_json_v1`、`binary_direction_sequence_v1` 三个真实 Parser；
- 标签注册表查询与 Canonical Sample 构造器；
- 原始 JSONL 到 Canonical JSONL 的批量转换；
- 转换失败报告和全量质量统计。

因此当前结论是“统一数据格式和质量门禁已经可执行”，不是“统一数据集已经生成”。

## 五、验证结果

| 检查 | 结果 |
|---|---|
| Canonical Schema | 1 个，通过 |
| Schema 引用 | 13 个，通过 |
| 数据集来源映射 | 11/11，通过 |
| 表达检测器 | 3/3，通过 |
| 任务许可配置 | 3/3，与阶段2 View Schema 一致 |
| 合法样例 | 4/4 接受 |
| 非法样例 | 7/7 拒绝 |
| Draft 2020-12 官方校验 | 通过 |
| 阶段1回归 | 通过 |
| 阶段2回归 | 通过 |
| 项目单元测试 | 6/6 通过 |

## 六、阶段产物

### Schema 与配置

- `schemas/canonical/canonical_traffic_sample.schema.json`
- `configs/canonical/representation_detection_v1.json`
- `configs/canonical/source_mapping_v1.json`

### 文档与记录

- `docs/canonical/canonical_sample_contract.md`
- `docs/canonical/source_field_mapping.md`
- `docs/implementation/phase3_execution_log.md`

### 校验

- `tests/fixtures/canonical/`
- `scripts/validate_canonical_contracts.py`
- `tests/test_canonical_contracts.py`
- `reports/phase3/canonical_contract_validation_v1.json`

## 七、下一阶段

阶段4实现真实数据转换层：

1. `tshark_packet_text_v1` parser；
2. `csic_http_json_v1` parser；
3. `binary_direction_sequence_v1` parser；
4. 标签注册表查询和 Canonical Sample 构造器；
5. JSONL 批量转换、失败报告、计数与小规模抽样验证；
6. 确认无误后再处理全部 544,381 条记录。
