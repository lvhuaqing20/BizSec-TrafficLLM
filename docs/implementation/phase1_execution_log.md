# 阶段1实施记录：任务与标签契约

## 1. 阶段目标

在不修改原始 `TrafficLLM_Datasets`、不生成完整训练数据、不训练模型的前提下，固定以下契约：

1. Business、Detection、Attack-Type 三个任务的训练目标与运行时结果格式；
2. TrafficLLM 各数据集可以参与哪些任务；
3. 每个原始标签到标准任务标签的映射；
4. 映射依据、证据边界和需要人工确认的争议项；
5. 可自动执行的静态校验规则。

## 2. 输入依据

- 原始数据目录：`/Users/odie/Documents/xm/TrafficLLM_Datasets`
- 原始源码目录：`/Users/odie/Documents/xm/TrafficLLM`
- 项目目录：`/Users/odie/Documents/xm/BizSec-TrafficLLM`

## 3. 操作记录

### 操作 1：检查工作区

- 检查日期：2026-08-27（Asia/Shanghai）
- 检查现有源码、测试和文档；未发现项目级 `AGENTS.md`。
- 项目目录不是独立 Git 仓库，因此本阶段通过文件清单、校验脚本和报告记录变更。
- 未修改任何原始数据文件。

### 操作 2：建立阶段1记录目录

- 新建本实施日志。
- 计划新增任务契约、JSON Schema、数据集任务矩阵、标签注册表生成器和静态校验器。

### 操作 3：定义任务契约和 Schema

- 创建 `docs/contracts/task_contract_v1.md`。
- 创建三个任务的训练目标 Schema 和运行时结果 Schema，共 6 个 JSON Schema。
- 明确区分原数据能够监督的字段和只能由运行时计算的字段。
- 明确 Detection 为 false 时由编排器产生 `attack_type=benign`，不调用 Attack-Type Adapter。

### 操作 4：生成标签注册表与数据审计证据

- 新建 `scripts/build_phase1_registry.py`，以只读方式解析原始标签文件和全部 train/test JSONL。
- 生成 `configs/labels/label_registry_v1.json`，包含所有 290 个声明标签。
- 生成 `reports/phase1/dataset_audit_v1.json`，记录每个 split 的原始输出和归一化输出计数。
- 实际解析 11 个数据集变体、510,416 条训练样本和 33,965 条测试样本，共 544,381 条；损坏 JSON 行为 0。
- 确认 `cw100-2024` 有 7 个声明标签未出现在任何样本中。
- 将 `iscx-botnet-2014/IRC` 标记为 `review_required=true`。

### 操作 5：建立数据集任务矩阵

- 新建 `docs/contracts/dataset_task_matrix.md`。
- 明确各数据集进入 Business、Detection、Attack-Type 的资格。
- 明确应用、网站、Tor、VPN 分类数据没有安全真值时不得自动映射为 benign。
- 明确 USTC 的 10 个正常应用与 10 个恶意家族划分。

### 操作 6：执行静态校验与回归测试

- 新建 `scripts/validate_phase1_contracts.py`。
- 注册表、审计计数、标签覆盖、任务资格、Detection/Attack-Type 逻辑不变量全部通过。
- 在临时目录重新生成注册表和审计报告，并使用 `cmp` 验证结果完全一致。
- 两个阶段1脚本通过 `py_compile`。
- 首次直接运行现有测试时因 `src/` 布局且项目未安装，出现 `ModuleNotFoundError`；使用正确命令 `PYTHONPATH=src python3 -m unittest discover -s tests -v` 后，4 项测试全部通过。
- 6 个 Schema 通过 JSON 语法检查。
- 当前 Python 环境未安装 `jsonschema`，Draft 2020-12 元 Schema 校验尚未执行；校验器已支持安装后自动执行。

### 操作 7：生成阶段1汇报

- 新建 `reports/phase1/phase1_summary.md`。
- 汇总完成内容、样本规模、验证结果、证据边界和待确认问题。

## 6. 阶段1产物清单

- `docs/contracts/task_contract_v1.md`
- `docs/contracts/dataset_task_matrix.md`
- `schemas/adapters/*.schema.json`（3 个）
- `schemas/pipeline/analysis_result.schema.json`（1 个）
- `configs/labels/label_registry_v1.json`
- `scripts/build_phase1_registry.py`
- `scripts/validate_phase1_contracts.py`
- `reports/phase1/dataset_audit_v1.json`
- `reports/phase1/contract_validation_v1.json`
- `reports/phase1/phase1_summary.md`

## 7. 阶段状态

技术校验通过，等待用户确认标签语义和任务边界后冻结阶段1并进入阶段2。

## 4. 设计决策记录

### D1：训练目标和运行时结果分离

原始 TrafficLLM 数据只提供类别标签，不提供证据代码。因此：

- 训练目标只包含能够从原数据得到监督的字段；
- 第一版训练目标和运行时结果均不包含 `confidence`；
- 后续只有在完成概率提取与校准设计后，才能通过新契约版本增加置信度。

### D2：未知安全语义不能映射为正常

应用分类、网站分类、Tor 行为和 VPN 行为数据没有攻击真值。相关样本的 Detection 标签保持 `null`，不能因为标签看起来是普通应用就默认标记为 benign。

### D3：攻击类型采用两级结构

- `attack_type` 表示稳定的大类，例如 `malware`、`botnet`、`apt`；
- `attack_family` 表示数据集提供的细分类别，例如 `Zeus`、`Neris`；
- 数据集没有细粒度标注时，`attack_family` 为 `null`。

### D4：阶段1数据处理边界

阶段1已经完成标签层数据处理，包括标签格式归一化、任务映射、分布统计和异常审计。阶段1没有处理 `instruction` 中的流量正文，没有把 TShark、HTTP 或方向序列转换成统一流量样本。

### D5：第一版不使用 confidence

用户确认第一版只直接输出分类结果。当时已从任务契约和早期演示数据结构中删除模型置信度字段；该规则型演示链路后来已从正式项目移除。

### D6：Adapter 训练目标和推理输出共用 Schema

删除 confidence 后，Business 和 Detection 的训练目标与推理输出完全一致；Attack-Type 的 `benign` 门控和编排元数据也不属于 Adapter。为减少重复和训练/推理格式漂移，6 个任务 Schema 合并为 3 个 Adapter Output Schema，另设 1 个 Pipeline Analysis Result Schema。

## 5. 后续操作

阶段1其余操作完成后继续追加：

- 任务输出契约；
- 数据集任务矩阵；
- 标签注册表生成结果；
- 静态校验结果；
- 待用户确认事项。

### 操作 8：删除第一版 confidence 并修正数据处理表述

- 根据用户决定，从 Business、Detection、Attack-Type 运行时契约删除 `confidence`。
- 同步修改 3 个运行时 JSON Schema 和契约校验样例。
- 当时同步删除早期演示结构中的置信度字段；相关演示代码后来已整体移除。
- 保留确定性 `risk_score`，因为它不是模型概率。
- 将阶段1准确表述为“已完成标签层数据处理，未完成流量内容与特征处理”。
- 修改后重新运行阶段1契约校验，结果为 `passed`。
- 重新运行现有 4 项回归测试，全部通过。
- 单样本推理输出检查确认不存在 `business_confidence` 和 `attack_confidence`。
- 单样本检查首次执行未给独立命令设置 `PYTHONPATH=src`，导致包导入失败；使用正确环境重跑后通过，该失败属于测试命令环境问题，不是业务代码错误。

### 操作 9：合并训练/推理 Schema

- 删除原 `schemas/tasks/` 下 6 个训练目标/运行时结果 Schema。
- 新建 3 个 Adapter 共享输出 Schema，训练目标与推理输出共用。
- 删除 Detection Adapter 中无监督的 `evidence_codes`。
- 删除 Attack-Type Adapter 中属于编排器的 `decision_source` 和 `benign` 类别。
- 新建 `schemas/pipeline/analysis_result.schema.json`，单独校验最终流水线结果及 `is_attack=false → attack_type=benign` 逻辑。
- 修改阶段1校验器，要求 Schema 集合恰好为 3 个 Adapter Schema 和 1 个 Pipeline Schema。
- 将 `jsonschema>=4.21,<5` 加入开发依赖，并在 README 记录新的 `--schema-root schemas` 校验命令。
