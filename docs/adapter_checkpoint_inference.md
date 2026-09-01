# PrefixEncoder Adapter 推理加载

`scripts/infer_chatglm2.py`支持两种明确区分的模式：

- 不传`--adapter-checkpoint`：仅运行基模，结果中的`inference_mode`为`base_model`。
- 传入`--adapter-checkpoint`：按任务训练配置构建PrefixEncoder，严格加载训练检查点，结果中的`inference_mode`为`prefix_adapter`。

Adapter模式会在生成前校验任务身份、检查点SHA256、参数名、形状、数据类型和有限值，并在加载后逐张量复核。最终`inference-result.json`中的`adapter_checkpoint`字段会保存训练元数据、检查点SHA256、加载参数量以及加载前后参数摘要，作为本次推理确实使用了对应任务Adapter的证据。

Adapter推理必须传入`--max-source-length`，接口还会用相邻的`pilot-training-result.json`自动核对其是否与训练一致。推理端会复用训练端的`build_prompt → tokenize → 截断 → generate`过程，并把原始长度、实际长度和是否截断写入结果，避免短程训练使用截断输入、推理却使用完整输入的偏差。

示例：

```bash
python scripts/infer_chatglm2.py \
  --task detection \
  --view-file artifacts/datasets/task_views/v1/examples/detection/csic-2010/train.jsonl \
  --model-dir /root/autodl-tmp/xm/models/chatglm2-6b \
  --adapter-checkpoint /root/autodl-tmp/xm/runs/SHORT_RUN/detection-TIMESTAMP/pytorch_model.bin \
  --output-dir /root/autodl-tmp/xm/runs/adapter-inference \
  --device cuda:0 \
  --max-source-length 256 \
  --max-length 512
```

短程训练检查点只用于验证工程闭环，不代表正式训练效果或最终指标。
