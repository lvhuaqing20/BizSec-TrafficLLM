# 三任务串行编排接口

## 1. 目标

本模块实现以下控制流：

```text
Canonical v1输入
→ Business View与Business Adapter
→ Business先验注入Detection View
→ Detection Adapter
→ is_attack门控
   ├─ false：跳过Attack-Type，输出benign
   └─ true：构建Attack-Type View并调用Attack-Type Adapter
→ Risk Fusion Backend
→ analysis_result.schema.json结构化输出
```

编排核心不绑定ChatGLM2、Prefix checkpoint或模拟规则。模型预测和风险融合分别通过`AdapterBackend`与`RiskFusionBackend`协议注入。

## 2. 新增模块

- `src/bizsec_trafficllm/orchestration/protocols.py`：Adapter和Fusion后端协议。
- `src/bizsec_trafficllm/orchestration/pipeline.py`：顺序调用、先验注入、门控和Schema验证。
- `scripts/validate_serial_pipeline.py`：使用真实Canonical v1输入与Scripted输出验证两条分支。
- `tests/test_orchestration_pipeline.py`：调用顺序、先验注入、门控、错误拒绝和确定性测试。

## 3. 输出文件

验证CLI在仓库外保存两个文件：

- `analysis-result.json`：严格满足现有Pipeline Schema的最终业务结果。
- `pipeline-trace.json`：后端名称、调用顺序、门控、每阶段输出、请求摘要和验证范围。

`model_backend=scripted-validation-adapter-v1`明确表明验证结果来自Scripted后端，不能解释为模型效果。

## 4. 验证命令

非攻击分支：

```bash
python scripts/validate_serial_pipeline.py \
  --scenario benign \
  --canonical-file artifacts/datasets/canonical/v1/canonical/ustc-tfc-2016/train.jsonl \
  --output-dir /mnt/18T/leijianuo/xm/runs/orchestration-validation
```

攻击分支：

```bash
python scripts/validate_serial_pipeline.py \
  --scenario attack \
  --canonical-file artifacts/datasets/canonical/v1/canonical/ustc-tfc-2016/train.jsonl \
  --output-dir /mnt/18T/leijianuo/xm/runs/orchestration-validation
```

两条命令都读取真实Canonical v1流量表示，但在执行前清空labels，避免把训练标签带入推理编排。Adapter和Fusion决策为固定验证值，只用于证明控制流、先验和Schema正确。

## 5. 真实模型接入边界

后续实现真实后端时，只需满足：

```python
predict(task, inference_request) -> task_output
```

真实后端负责按任务加载对应Prefix checkpoint并返回符合Adapter Schema的JSON。编排核心、Business先验注入、Detection门控和最终输出组装不需要改写。
