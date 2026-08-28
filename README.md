# BizSec-TrafficLLM

TrafficLLM数据准备、三任务View构造、训练消息序列化与ChatGLM2训练输入适配项目。当前正式范围是：原始TrafficLLM数据审计、Canonical统一化、Business/Detection/Attack-Type训练样本、Messages Dataset，以及ChatGLM2-6B/P-Tuning v2输入契约。

项目不再包含早期规则型API、规则推理后端或模拟风险融合。真实Tokenizer、Adapter训练和vLLM推理将在后续阶段接入。

## 正式处理链路

```text
TrafficLLM_Datasets（只读）
        ↓
标签审计与标签注册表
        ↓
三种真实Parser
        ↓
CanonicalTrafficSample
        ↓
Representation Selector + View Engine
        ↓
Business / Detection / Attack-Type结构化训练样本
        ↓
模型无关SFT Messages Dataset
```

## 项目目录

```text
├── src/bizsec_trafficllm/
│   ├── data/                   原始流量到Canonical的正式处理代码
│   ├── views/                  Canonical到三任务View的正式构造代码
│   ├── serialization/          Task View到训练/推理Messages
│   └── tokenization/           ChatGLM2输入、label mask与长度审计
├── configs/                    标签、数据源、隐私和View策略
├── schemas/                    Canonical、View、训练和Adapter输出契约
├── scripts/                    生成及独立校验命令
├── tests/                      单元测试与契约fixtures
├── artifacts/datasets/         全量可训练数据产物（不纳入Git）
├── reports/                    小型审计、验证报告和阶段总结
└── docs/                       设计文档与实施日志
```

详细说明见 [项目结构说明](docs/project_structure.md)。

数据处理、Canonical统一化和三任务View Engine的完整设计与实现说明见
[数据处理与任务视图构造设计实现文档](docs/design/data_processing_and_view_engine_design.md)。

## 安装与测试

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
PYTHONPATH=src python -m unittest discover -s tests -v
```

## 契约校验

```bash
python scripts/validate_phase1_contracts.py \
  --registry configs/labels/label_registry_v1.json \
  --audit reports/phase1/dataset_audit_v1.json \
  --schema-root schemas \
  --report reports/phase1/contract_validation_v1.json

python scripts/validate_view_contracts.py \
  --schema-root schemas \
  --config-root configs/views \
  --fixtures-dir tests/fixtures/views \
  --report reports/phase2/view_contract_validation_v1.json

python scripts/validate_canonical_contracts.py \
  --schema-root schemas \
  --config-root configs/canonical \
  --fixtures-dir tests/fixtures/canonical \
  --registry configs/labels/label_registry_v1.json \
  --audit reports/phase1/dataset_audit_v1.json \
  --report reports/phase3/canonical_contract_validation_v1.json
```

## 全量Canonical数据

生成：

```bash
python scripts/convert_trafficllm_dataset.py \
  --data-root ../TrafficLLM_Datasets \
  --dataset all \
  --split all \
  --output-dir artifacts/datasets/canonical/v1
```

校验：

```bash
python scripts/validate_converted_samples.py \
  --input-dir artifacts/datasets/canonical/v1 \
  --schema-root schemas \
  --expected-records 544381 \
  --report reports/phase4/full_validation_v1.json
```

结果：544,381条成功、0失败、0重复。

## 全量三任务训练样本

生成：

```bash
python scripts/build_task_views.py \
  --canonical-dir artifacts/datasets/canonical/v1/canonical \
  --output-dir artifacts/datasets/task_views/v1 \
  --schema-root schemas \
  --config-root configs/views \
  --task all
```

校验：

```bash
python scripts/validate_task_views.py \
  --input-dir artifacts/datasets/task_views/v1 \
  --schema-root schemas \
  --expected-business 390279 \
  --expected-detection 170423 \
  --expected-attack-type 100462 \
  --report reports/phase5/full_views_validation_v1.json
```

结果：Business 390,279条、Detection 170,423条、Attack-Type 100,462条，共661,164条。

## 全量Messages Dataset

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

结果：661,164条训练消息、0重复、0错误；train 618,152条，test 43,012条。

## 当前边界与下一阶段

当前已经保存结构化 `view + target` 和模型无关Messages Dataset，并固定使用ChatGLM2-6B原生Tokenizer与P-Tuning v2。ChatGLM2输入适配和answer-only label mask已经实现；真实Tokenizer全量审计和GPU训练尚未执行。

下一阶段依次实现：

1. 在服务器锁定ChatGLM2兼容的PyTorch/Transformers/CUDA环境；
2. 执行真实Tokenizer全量审计并固定长度；
3. 实现三个Prefix checkpoint的统一训练入口；
4. 实现ChatGLM2本地推理；
5. 实现Business → Detection → Attack-Type门控编排。
