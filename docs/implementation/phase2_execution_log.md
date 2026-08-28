# 阶段2实施记录：任务 View 契约与字段策略

## 1. 阶段目标

在不转换完整 TrafficLLM 流量、不生成完整训练集、不训练模型的前提下，定义并验证：

1. 三个 Adapter 的 View 输入结构；
2. packet、HTTP request、direction sequence 三种流量表示；
3. 字段白名单、匿名化、优先级和标签泄漏规则；
4. Business 结果向 Detection View 的注入格式；
5. Token 超限时的确定性裁剪策略；
6. 合法与非法 View 样例以及可重复执行的校验器。

## 2. 已冻结的阶段1约束

- Adapter 输出不包含 `confidence`；
- Business、Detection、Attack-Type 的训练目标与推理输出分别共用同一个 Schema；
- Detection 只输出 `is_attack`；
- Attack-Type 只在 `is_attack=true` 时调用；
- `benign` 由流水线 gate 生成，不属于 Attack-Type Adapter 类别；
- 原始标签、训练目标和候选类别列表不得进入 View。

## 3. 操作记录

### 操作 1：建立阶段2目录与实施记录

- 创建本日志。
- 阶段2配置采用 JSON，以便使用 Python 标准库完成本地校验；JSON 是 YAML 1.2 的兼容子集，后续可以无损转换为 YAML。
- 本阶段只处理 View 契约与少量人工样例，不读取或改写全部 TrafficLLM 流量正文。

### 操作 2：定义共享结构与三个 View Schema

- 新建 `schemas/views/shared_definitions.schema.json`，定义 packet、HTTP request、direction sequence、统计字段、Business Prior 和质量元数据。
- 新建 Business、Detection、Attack-Type 三个 View Schema。
- Business 支持三种现有 TrafficLLM 表示；Detection 与 Attack-Type v1 支持 packet 和 HTTP request。
- Business View 的 `priors` 必须为空；Detection/Attack-Type 仅允许结构化 Business Prior 或 null。
- 三个 View 均禁止额外顶层字段，Prompt、标签和编排器信息不能通过未声明字段进入。

### 操作 3：建立字段、Token 和泄漏策略

- 新建 `configs/views/field_registry_v1.json`，记录字段路径、类型、允许 View、优先级、转换、敏感性和泄漏风险。
- 精确 IP 和 MAC 不进入 v1 View，IP 转换为 internal/external/unknown 角色。
- 新建 `token_budget_v1.json`，定义 P0-P3 优先级、各 View 内容字符上限和确定性裁剪顺序；实际 Token 上限由部署模型配置提供。
- 新建 `leakage_policy_v1.json`，禁止原始 output/label/target/prompt/candidate list、confidence、evidence_codes 和 decision_source 进入 View。
- 泄漏检查不简单禁止所有类别字符串；真实 SNI/Host 可能自然包含应用名称，需依据字段来源而不是字符串相等判断。

### 操作 4：编写 View 设计文档

- 创建公共 View 契约和 Business、Detection、Attack-Type 三份任务说明。
- 创建字段策略和 Token 预算说明。
- 明确三种表示的粒度对应关系、null 与 unknown 的区别、P0 超限失败规则和隐私处理边界。

### 操作 5：建立 View 合法与非法样例

- 创建 4 个合法样例，覆盖 Business packet、Business direction sequence、Detection HTTP 和 Attack-Type packet。
- 创建 5 个非法样例，覆盖 ground truth 泄漏、confidence Prior、重复 Detection Prior、粒度不一致和原 Prompt 片段泄漏。
- 创建 fixture manifest，明确每个样例对应的 Schema 和预期校验规则。

### 操作 6：实现阶段2 View 校验器

- 新建 `scripts/validate_view_contracts.py`。
- 校验字段注册表唯一性、View/优先级合法性、Token 策略字段引用和强制泄漏规则。
- 校验 View 版本、任务、表示类型、粒度对应关系、Prior 结构、禁止字段和 Prompt 泄漏片段。
- 安装 `jsonschema` 时额外执行 Draft 2020-12 与跨文件 `$ref` 校验；未安装时仍执行标准库结构和语义校验。

### 操作 7：首次验证并接入回归测试

- 阶段2校验首次运行通过：4 个合法样例全部接受，5 个非法样例全部拒绝。
- 阶段1校验在新增 View Schema 后仍通过，说明两个阶段的 Schema 范围已经隔离。
- 新建 `tests/test_view_contracts.py`，通过临时报告文件执行阶段2校验器，避免测试污染正式报告。
- README 增加阶段2完整校验命令。

### 操作 8：增加零依赖 Schema 引用检查

- 校验器新增 View Schema 文件集合检查。
- 遍历所有 `$ref`，验证目标 `$id` 和 JSON Pointer 存在。
- 该检查不依赖 `jsonschema`，可在本地和最小化训练服务器环境执行。

### 操作 9：最终 QA 与阶段汇报

- 所有 View Schema、配置和 fixture 通过 JSON 语法检查。
- 阶段1和阶段2校验均为 passed。
- 项目单元测试 5/5 通过。
- 合法 fixture 中未发现 confidence、ground truth、候选类别、decision_source、evidence_codes 或原 Prompt 片段。
- 生成 `reports/phase2/phase2_summary.md`。

### 操作 10：补充流量表达选择依据

- 记录三种流量表达的来源：原始样本实际结构决定可构造的表达，任务 View Schema 决定允许使用的表达。
- 明确数据集配置只声明预期格式，转换器仍须检查样本内容；配置与内容不一致时进入解析/转换失败报告，不得强制转换。
- 明确未来统一样本可以保存多种可复现表示，但单个任务 View 只选择一种主要表示。
- 本次仅补充设计契约和汇报记录，现有 Schema 已体现当前任务许可，因此无需修改代码与 Schema。

## 5. 阶段2产物清单

- `schemas/views/*.schema.json`（3 个任务 View + 1 个共享定义）
- `configs/views/field_registry_v1.json`
- `configs/views/token_budget_v1.json`
- `configs/views/leakage_policy_v1.json`
- `docs/views/*.md`（6 份）
- `tests/fixtures/views/*.json`（4 个合法、5 个非法、1 个 manifest）
- `scripts/validate_view_contracts.py`
- `tests/test_view_contracts.py`
- `reports/phase2/view_contract_validation_v1.json`
- `reports/phase2/phase2_summary.md`

## 6. 阶段状态

阶段2技术校验通过，等待用户审阅后进入阶段3统一样本 Schema。

## 4. 设计决策

### D1：View 与 Prompt 分离

View 是结构化 JSON；模型 Chat Template 和自然语言任务指令由后续 Serializer 产生，不写进 View。

### D2：Detection Prior 不重复注入 Attack-Type

Detection v1 只输出 `is_attack`，而 Attack-Type 仅在其为 true 时调用，因此不在 Attack-Type View 中重复注入 `is_attack=true`。

### D3：Business Prior 允许为 null

Detection/Attack-Type 训练数据不一定具备可用的 Business 预测。`null` 表示没有先验；`business_type=unknown` 表示 Business Adapter 实际执行但无法识别，两者不能混淆。

### D4：流量表达由原始结构与任务许可共同确定

原始样本实际内容决定可构造 `packet`、`http_request` 或 `direction_sequence` 中的哪些表示；目标任务的 View Schema 再做许可检查。两者不同时满足时不生成该任务 View。数据集名称和配置只用于解析路由与审计，不能替代内容检查。
