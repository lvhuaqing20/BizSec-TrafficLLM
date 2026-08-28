# Prompt与Messages契约 v1

## 1. 目标

把阶段5的结构化 `view + target` 确定性转换成模型无关的聊天消息数据。该层不调用Tokenizer，不添加任何基础模型专用特殊Token。

## 2. 消息格式

训练样本固定为三条消息：

```text
system     固定任务指令
user       Canonical JSON序列化后的任务View
assistant  Canonical JSON序列化后的任务target
```

推理请求固定为两条消息：

```text
system     与训练完全相同的任务指令
user       与训练使用相同规则序列化的任务View
```

推理请求不包含assistant消息。

## 3. 确定性JSON

View和target使用 `canonical-json-v1`：

- UTF-8和原始Unicode；
- 键按字典序排序；
- 使用紧凑分隔符，不增加无意义空格和换行；
- 禁止NaN和Infinity；
- 不使用Markdown代码块；
- 不添加 `Answer:` 等额外前缀。

## 4. 三任务输出

- Business：`business_domain`、`business_type`；
- Detection：仅`is_attack`；
- Attack-Type：`attack_type`、`attack_family`。

三个任务均复用阶段1冻结的Adapter Output Schema。Prompt和输出不包含置信度字段。

## 5. 训练与推理一致性

训练和推理共享同一个Prompt配置 `prompt_templates_v1.json`。基础模型确定后，训练服务器使用模型原生 `tokenizer.apply_chat_template(messages)`，不在本地消息数据中硬编码Qwen或其他模型的控制Token。

## 6. Business Prior边界

当前Detection和Attack-Type训练消息继承阶段5基线View，Business prior为`null`。这是独立Adapter基线，不代表最终级联增强数据。Business Adapter训练完成后，应使用out-of-fold预测构造包含真实预测误差的prior增强版本，不能直接用业务真值伪造上游预测。

## 7. 版本

- Prompt配置：`prompt-templates-v1`；
- JSON序列化：`canonical-json-v1`；
- 训练消息：`training-message-example-v1`；
- 推理消息：`inference-message-request-v1`。
