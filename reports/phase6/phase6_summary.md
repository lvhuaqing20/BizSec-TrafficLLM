# 阶段6总结：Messages Dataset

阶段6A Prompt契约、阶段6B全量Messages Dataset均已完成。

| 项目 | 数量 |
|---|---:|
| Business | 390,279 |
| Detection | 170,423 |
| Attack-Type | 100,462 |
| 合计 | 661,164 |
| train | 618,152 |
| test | 43,012 |
| 文件 | 66 |
| 重复sample/task | 0 |
| 校验错误 | 0 |

全量数据位于 `artifacts/datasets/messages/v1`。每条训练记录固定包含system、user、assistant三条消息；User只包含View，Assistant只包含target。

组合SHA-256：`d8ec9e8e85d288c149f07d9764cfdb2a6a17cde93c6634ed9c0c2134fea8e17a`。

项目正式测试23/23通过，阶段1—3契约回归全部通过。

下一步是阶段6C：确定基础模型准确版本后，在远程服务器加载统一Tokenizer，应用模型原生Chat Template并生成token长度与截断审计报告。
