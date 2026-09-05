# BizSec-TrafficLLM

TrafficLLM数据准备、三任务View构造、训练消息序列化与ChatGLM2训练推理接口项目。本工作分支包含原始数据管线，以及后续PrefixEncoder训练、checkpoint加载、单任务推理、串行编排和验证工具。工程链路跑通不代表三任务精度已达标。

## 给协作者的最新入口（2026-09-05）

两种Business方案的实际代码、运行参数及已有结果集中在 **[experiments/README.md](experiments/README.md)**：

- [方案①：TrafficLLM论文式训练方法](experiments/paper-style/README.md)：官方Stage-2 Trainer训练CSTNET 20类BizSec单包/JSON任务，含数据转换、训练、续训、验证、依赖和上游补丁。
- [方案②：减少业务标签](experiments/reduced-labels/README.md)：保留5个数据集、150个标签的统一Business Adapter，含筛选、训练入口、验证worker及20点曲线。

最新公开证据包括方案①6000步固定validation Accuracy 62.50%，以及方案②最佳19000步18.33%；**两者数据/标签/划分不同，不能直接比较，也不是最终test成绩**。

`main`保留最初基线；新增实现位于`feature/training-inference-interfaces`。历史文档和审计报告记录的是各自阶段，不应当作当前完成状态。数据集、模型、环境、checkpoint和日志均不纳入Git。

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
├── scripts/                    数据生成、训练、推理和验证入口
├── experiments/                两种Business方案、启动脚本及聚合结果
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

## 当前接口与实验边界

- [训练与单任务推理接口](docs/interfaces/training_inference_interface.md)：Messages读取、Tokenizer适配、answer-only label mask、真实前向和生成。
- [Checkpoint推理](docs/adapter_checkpoint_inference.md)：PrefixEncoder保存、加载与参数检查。
- [串行编排](docs/interfaces/orchestration_interface.md)：Business → Detection → 有攻击时Attack-Type → 结构化输出；已有工程验证，不代表效果验收完成。
- [两种Business方案](experiments/README.md)：公开已有实验代码和聚合验证结果，正式最终test与端到端精度结论仍待后续确认。

克隆代码不会自动获得数据或模型；运行GPU实验前请阅读对应方案的环境、数据和输出目录要求。上面的数据管线统计属于初始基线报告。
