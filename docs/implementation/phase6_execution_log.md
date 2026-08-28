# 阶段6实施日志：Prompt序列化与Messages Dataset

## 1. 阶段目标

实现Task View到模型无关聊天消息的确定性转换，生成全量SFT文本数据，并验证View/target隔离、train/test边界、任务Schema和内容可重复性。

## 2. 操作记录

### 操作1：冻结Prompt策略

- 三任务各自使用固定英文System Prompt。
- User只包含紧凑JSON View，Assistant只包含紧凑JSON target。
- 不自定义特殊Token，后续使用基础模型原生Chat Template。
- 训练和推理共享System/User模板；推理不包含Assistant消息。

### 操作2：消息契约与Serializer

- 建立训练消息和推理消息两个Draft 2020-12 Schema。
- 实现确定性JSON、Prompt模板加载、任务View校验、target校验和消息校验。
- Serializer拒绝任务与View不匹配、sample ID不一致、非法target和非法split。

### 操作3：生成与独立校验脚本

- `build_training_messages.py`逐文件流式生成消息JSONL并记录文件哈希。
- `validate_training_messages.py`与来源Task View逐行对照。
- 校验User等于源View、Assistant等于源target、角色顺序、任务Schema、文件集合、记录数量、唯一性和train/test路径。

### 操作4：Pilot

- 对每个任务文件最多读取5条，共生成170条消息。
- Business 70、Detection 50、Attack-Type 50。
- 66个文件、0重复、0错误，组合SHA-256为 `f1c3116349cefa7f868ff89d77707ccbd92d45224e464fff6b32b06e92c66f05`。

### 操作5：全量生成与验证

- 读取661,164条Task View样本，全部生成成功。
- Business 390,279、Detection 170,423、Attack-Type 100,462。
- train 618,152、test 43,012；原数据split路径保持不变。
- 66个文件、661,164个唯一sample/task组合、0重复、0错误。
- Messages Dataset组合SHA-256为 `d8ec9e8e85d288c149f07d9764cfdb2a6a17cde93c6634ed9c0c2134fea8e17a`。

### 操作6：回归测试

- 新增6项Serializer测试，覆盖确定性、紧凑JSON、训练/推理消息差异、答案隔离、非法target和任务View错配。
- 项目正式测试23/23通过，Python语法检查通过。
- 阶段1标签契约、阶段2 View契约和阶段3 Canonical契约全部回归通过。

## 3. 阶段边界

已完成可供SFT读取的文本消息数据，但尚未执行基础模型Chat Template、Tokenizer、token长度审计、label mask或Adapter训练。这些依赖准确的基础模型和Tokenizer版本，属于下一阶段。
