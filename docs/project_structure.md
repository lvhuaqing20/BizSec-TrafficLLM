# 项目结构说明

## 1. 正式范围

项目当前只保留真实TrafficLLM数据准备、三任务View构造、模型无关消息序列化和ChatGLM2训练输入适配，不包含规则模拟API、规则Backend、模拟Pipeline或风险融合。

正式数据流：

```text
TrafficLLM_Datasets
  → 标签审计/注册表
  → Parser与Canonical Builder
  → CanonicalTrafficSample
  → Representation Selector与View Engine
  → Business/Detection/Attack-Type训练样本
  → SFT Messages Dataset
  → ChatGLM2 query/response与P-Tuning v2特征
```

## 2. 顶层目录职责

| 目录 | 职责 | 是否生成物 |
|---|---|---|
| `src/` | 可复用Python实现 | 否 |
| `configs/` | 可版本化策略 | 否 |
| `schemas/` | JSON结构契约 | 否 |
| `scripts/` | 生成与独立校验入口 | 否 |
| `tests/` | 单元测试和固定fixtures | 否 |
| `docs/` | 当前设计和历史实施记录 | 否 |
| `artifacts/datasets/` | 全量Canonical、Task View和Messages数据 | 是 |
| `reports/` | 小型验证结果与总结 | 是 |

数据处理和任务视图的统一设计说明见
`docs/design/data_processing_and_view_engine_design.md`。

## 3. 源码分层

### `src/bizsec_trafficllm/data/`

负责原始数据到Canonical：三种Parser、路由、标签解析、隐私处理、统一样本构造、运行时校验和流式转换。

### `src/bizsec_trafficllm/views/`

负责Canonical到任务View：表达选择、预Token预算、Business/Detection/Attack-Type View构造、Schema校验以及训练样本封装。

### `src/bizsec_trafficllm/serialization/`

负责Task View到训练/推理消息：加载三任务Prompt、确定性JSON序列化、任务View和target校验，以及模型无关的messages封装。

### `src/bizsec_trafficllm/tokenization/`

负责Messages到ChatGLM2训练输入：system/user组合、原生`build_prompt`、answer-only label mask，以及真实Tokenizer长度审计。该层不保存第二份全量数据。

## 4. 数据产物

```text
artifacts/datasets/
├── canonical/v1/
│   ├── canonical/          22个全量Canonical JSONL
│   ├── failures/           22个失败审计JSONL
│   └── conversion_report.json
├── task_views/v1/
    ├── examples/
    │   ├── business/
    │   ├── detection/
    │   └── attack_type/
    └── generation_report.json
└── messages/v1/
    ├── examples/
    │   ├── business/
    │   ├── detection/
    │   └── attack_type/
    └── generation_report.json
```

`artifacts/datasets/`体积较大且可由脚本重复生成，因此已加入 `.gitignore`。验证报告单独存放在 `reports/`，便于版本管理和汇报。

## 5. 依赖方向

```text
scripts
  ├── configs
  ├── schemas
  └── src/bizsec_trafficllm
          ├── data
          ├── views
          └── serialization

tests → src + configs + schemas + fixtures
```

`data`不依赖`views`；`views`消费Canonical结构；`serialization`消费Task View。后两层都不读取原始TrafficLLM文件。报告和文档不能被运行时代码反向依赖。

## 6. 后续新增模块

后续只在真实能力落地时创建目录：

- `training/`：ChatGLM2 P-Tuning v2训练入口和运行清单；
- `inference/`：ChatGLM2三个Prefix checkpoint推理；
- `orchestration/`：Business → Detection → Attack-Type门控。

不预建空目录，不再添加规则模拟实现。
