# ChatGLM2训练与推理接口

## 1. 范围

本接口只使用Messages v1和Task Views v1，不读取排除的数据v2。

当前目标是验证两条真实链路：

1. Messages v1 → ChatGLM2特征 → Prefix模型前向 → 有限loss；
2. Task View v1 → 推理Messages → ChatGLM2生成 → 原始输出与Schema检查。

训练冒烟不创建optimizer、不执行backward、不更新参数、不保存checkpoint。推理结果只表示接口执行成功，Schema是否通过单独报告；未加载任务checkpoint时不能作为准确率结论。

## 2. 训练接口

核心实现：

- `src/bizsec_trafficllm/training/dataset.py`
- `src/bizsec_trafficllm/training/interface.py`

服务器示例：

```bash
/mnt/18T/leijianuo/xm/envs/bizsec-chatglm2/bin/python \
  scripts/smoke_training_interface.py \
  --task detection \
  --model-dir /mnt/18T/leijianuo/xm/models/chatglm2-6b \
  --output-dir /mnt/18T/leijianuo/xm/runs/interface-smoke/training \
  --device cuda:0 \
  --max-samples 1
```

通过标准：

- 只读取train并按sample ID确定性划分验证集；
- Batch包含`input_ids`、`attention_mask`和answer-only `labels`；
- 只有PrefixEncoder参数可训练；
- loss为有限数值；
- 没有optimizer、backward或checkpoint。

## 3. 推理接口

核心实现：

- `src/bizsec_trafficllm/inference/interface.py`

服务器示例：

```bash
/mnt/18T/leijianuo/xm/envs/bizsec-chatglm2/bin/python \
  scripts/infer_chatglm2.py \
  --task detection \
  --view-file artifacts/datasets/task_views/v1/examples/detection/csic-2010/train.jsonl \
  --model-dir /mnt/18T/leijianuo/xm/models/chatglm2-6b \
  --output-dir /mnt/18T/leijianuo/xm/runs/interface-smoke/inference \
  --device cuda:0
```

推理接口保存：

- `sample_id`和任务；
- 完整原始模型输出；
- JSON解析结果或解析错误；
- 任务输出Schema校验结果；
- 推理耗时和运行元数据。

## 4. 当前不包含

- 正式训练循环；
- optimizer、梯度累积和checkpoint保存；
- Adapter checkpoint加载；
- Business → Detection → Attack-Type串行门控；
- 准确率、F1或最终效果结论。

这些能力应在单任务接口稳定并取得三个checkpoint后继续实现。
