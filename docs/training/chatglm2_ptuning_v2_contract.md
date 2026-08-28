# ChatGLM2-6B + P-Tuning v2训练契约

## 1. 固定选择

- 基础模型与Tokenizer：`THUDM/chatglm2-6b`；
- 模型加载：`AutoModel.from_pretrained(..., trust_remote_code=True)`；
- Prompt构造：ChatGLM2原生`tokenizer.build_prompt`；
- 微调方法：P-Tuning v2；
- `pre_seq_len=128`，`prefix_projection=false`；
- 冻结基础模型、Embedding和LM Head，仅训练PrefixEncoder；
- Business、Detection、Attack-Type分别保存独立Prefix checkpoint。

机器可检查配置见`configs/models/chatglm2_6b_ptuning_v2.json`，Schema见
`schemas/training/chatglm2_ptuning_model_contract.schema.json`。

## 2. Messages到ChatGLM2输入

Messages Dataset仍是唯一训练数据源，不生成第二套全量文本数据。运行时进行以下转换：

```text
system + "\n\nTraffic view:\n" + user → query
assistant                              → response
query → tokenizer.build_prompt         → prompt
```

训练特征：

```text
input_ids = prompt_ids + response_ids + eos
labels    = -100       + response_ids + eos
```

Prompt和padding位置均为`-100`，因此只对assistant答案计算loss。转换实现位于
`src/bizsec_trafficllm/tokenization/chatglm2.py`。

## 3. 长度策略

当前三个任务的长度配置属于临时上限，状态为
`provisional_until_token_audit`。必须使用ChatGLM2原生Tokenizer执行全量审计，
再决定最终`max_source_length`和`max_target_length`，并把状态改为
`validated_by_token_audit`。

审计报告至少包含：记录数、P50/P90/P95/P99、最大长度、source/target截断数量与比例。

## 4. 数据集边界

- 当前`test`只允许用于最终评估；
- 验证集从`train`按sample ID确定性哈希划分5%；
- 三个任务的checkpoint输出目录必须互不相同；
- 当前Detection和Attack-Type消息中的Business Prior仍为空，不能把三任务独立基线误报为最终级联系统。

## 5. 可复现性

首次下载允许请求`revision=main`。开始完整训练前，必须在运行清单中记录实际解析到的模型commit和Tokenizer指纹，不能只记录`main`。
