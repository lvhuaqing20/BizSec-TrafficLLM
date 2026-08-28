# 阶段5实施日志：真实 View Engine

## 1. 阶段目标

将阶段4的全量 `CanonicalTrafficSample` 确定性转换为三个任务View，并建立训练产物、推理构造接口、Schema校验、防泄漏约束和可重复审计。

## 2. 操作记录

### 操作 1：表达选择策略

- 新建 `representation_selection_v1.json`。
- Business允许packet、HTTP和direction sequence；Detection与Attack-Type只允许packet和HTTP。
- 优先使用Canonical主表达；只有主表达不适用于任务时才按固定顺序fallback并记录warning。

### 操作 2：View Engine实现

- 实现 `RepresentationSelector`、`PreTokenBudgetManager`、`ViewValidator` 和 `ViewEngine`。
- 提供 `build_business`、`build_detection`、`build_attack_type` 三个推理接口。
- Detection和Attack-Type仅接收Schema化Business先验；Business禁止先验；Attack-Type禁止Detection答案先验。

### 操作 3：训练样本契约与生成器

- 新建统一 `TaskTrainingExample` Schema，结构为sample ID、task、view、target。
- target始终位于View外，防止标签泄漏。
- 训练数据的Business prior固定为`null`，不使用标签伪造上游模型输出。

### 操作 4：分层样本验证

- 对阶段4的1,582条标签分层样本生成View。
- 生成Business 1,456、Detection 186、Attack-Type 102，共1,744条。
- 独立校验66个文件，0重复、0错误。

### 操作 5：全量生成与独立校验

- 读取544,381条Canonical记录。
- 生成Business 390,279、Detection 170,423、Attack-Type 100,462，共661,164条。
- 所有无相应任务target的样本按契约跳过，不算失败。
- 独立验证66个文件、661,164个唯一sample/task组合、0重复、0错误。
- 组合SHA-256：`93af8d43eeeef338ea7a9ebcd05ab2cd9917b4c7c086759baa13c9f71d91362e`。

### 操作 6：自动化测试与回归

- 覆盖三种表达、任务不可用、业务先验注入、Attack-Type无检测答案、字符预算和target隔离。
- 阶段5首次完成时项目单元测试21/21通过；正式结构整理移除4项旧规则演示测试后，保留的正式测试17/17通过。
- 阶段1任务/标签契约、阶段2 View契约、阶段3 Canonical契约全部回归通过。

## 3. 阶段边界

已完成结构化View构造及训练样本生成。尚未实现真实模型prompt序列化、统一tokenizer精确计数、远程vLLM调用、三Adapter加载和端到端推理评估；这些属于下一阶段。
