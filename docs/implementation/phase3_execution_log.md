# 阶段3实施日志：CanonicalTrafficSample

## 1. 阶段目标

建立原始 TrafficLLM 数据与任务 View 之间的统一样本契约，冻结来源追踪、表达容器、标签隔离、质量语义和机器校验规则。本阶段不批量转换原数据。

## 2. 操作记录

### 操作 1：审计已有契约和真实样例

- 检查阶段1标签注册表、数据审计、任务矩阵和阶段2 View Schema。
- 只读抽查 app53-2023、csic-2010、cw100-2018 原文件开头记录。
- 确认原文件为 JSONL，顶层只有 instruction/output，流量正文嵌在 instruction。
- 确认三类实际结构：TShark 包文本、HTTP request JSON、方向二进制序列。

### 操作 2：建立 CanonicalTrafficSample Schema

- 新建 `schemas/canonical/canonical_traffic_sample.schema.json`。
- 定义稳定 SHA-256 sample ID、dataset/live 来源、三种可并存表达、上下文、隔离标签和质量/隐私元数据。
- 复用阶段2的 packet、HTTP、direction 和 statistics 定义，避免同一表示在两个阶段发生字段漂移。
- 明确失败记录不属于有效 CanonicalTrafficSample。

### 操作 3：建立表达识别和来源映射配置

- 新建 `representation_detection_v1.json`，定义实际内容检查、冲突失败策略和任务许可矩阵。
- 新建 `source_mapping_v1.json`，完整覆盖 11 个数据集变体。
- 固定 sample ID 组成字段和 0 起始记录序号。
- 配置只表达预期格式，不能覆盖实际内容检查结果。

### 操作 4：建立合法与非法样例

- 创建 4 个合法样例：packet、HTTP、direction、实时多表示。
- 创建 7 个基于合法样例的确定性变异：错误 ID、无表达、主表达不存在、可用列表不一致、任务标签不一致、来源/主表达冲突、不安全路径。
- 变异样例避免复制整份大 JSON，便于审阅和维护。

### 操作 5：实现阶段3校验器与测试

- 新建 `scripts/validate_canonical_contracts.py`。
- 校验 11 个来源映射与阶段1审计一致。
- 从阶段2 View Schema 实际读取三个任务的表达许可，并与阶段3配置比较。
- 校验所有 Schema 引用、稳定 sample ID、来源安全、表达一致性、标签逻辑和隐私状态。
- 安装 jsonschema 时执行 Draft 2020-12 官方校验；同时始终执行零依赖项目不变量检查。
- 新建 `tests/test_canonical_contracts.py`，报告写入临时目录，不污染正式报告。

### 操作 6：首次阶段3验证

- 阶段3状态 passed。
- 11/11 数据集来源映射通过。
- 3/3 表达检测器与 3/3 任务许可配置通过。
- 4/4 合法样例接受，7/7 非法样例拒绝。
- 13 个 Canonical Schema 引用解析通过。

### 操作 7：跨阶段回归

- 阶段1校验 passed：544,381 条审计记录、290 个标签保持一致。
- 阶段2校验 passed：View Schema、字段策略、泄漏策略和正反样例保持一致。
- Python 编译检查通过。
- 项目单元测试 6/6 通过。

### 操作 8：补充文档与汇报

- 新建统一样本契约和来源字段映射文档。
- README 增加阶段3可重复执行命令。
- 生成正式校验报告与阶段总结。

### 操作 9：补充具体实现说明与边界记录

- 在公共契约中记录 Schema、配置、校验和测试四层实现方式。
- 明确当前已经实现统一结构、11 个来源映射、3 种表达规则、标签隔离、来源追踪和自动校验。
- 明确当前尚未实现真实 Parser、Canonical Sample 构造器、JSONL 批量转换和失败审计。
- 汇报时不得将“统一样本契约通过”表述为“544,381 条原始记录已经完成转换”。

## 3. 设计决策

### D1：Canonical Sample 不是 Prompt

完整 instruction 只作为 parser 输入，不保存到统一样本，也不能进入任务 View。

### D2：标签与流量物理分区

训练数据允许保存 labels，但 View Builder 只能读取 traffic/context/受控 quality；推理样本 labels 为 null。

### D3：允许多表示，单 View 只选一种

未来 PCAP 可以生成 packet 和 HTTP 等多个可复现表达。统一样本可同时保存，任务 View 仍只选择一种主表达。

### D4：失败不伪装为 partial

`partial` 只表示仍能满足一个合法表示的必要字段。没有有效表示、格式冲突、歧义或隐私失败必须进入失败报告。

### D5：来源路径相对化

统一样本只保存数据集根目录相对路径，避免机器相关绝对路径泄漏并提升跨环境可重复性。

## 4. 阶段产物

- `schemas/canonical/canonical_traffic_sample.schema.json`
- `configs/canonical/representation_detection_v1.json`
- `configs/canonical/source_mapping_v1.json`
- `docs/canonical/canonical_sample_contract.md`
- `docs/canonical/source_field_mapping.md`
- `tests/fixtures/canonical/`（4 个合法、7 个非法变异、1 个 manifest）
- `scripts/validate_canonical_contracts.py`
- `tests/test_canonical_contracts.py`
- `reports/phase3/canonical_contract_validation_v1.json`
- `reports/phase3/phase3_summary.md`

## 5. 阶段状态

阶段3技术实现与回归校验通过。下一阶段是实现 3 个真实数据解析器及批量转换/失败审计，不在本阶段提前处理全部数据。
