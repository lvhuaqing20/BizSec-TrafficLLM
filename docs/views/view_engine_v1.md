# View Engine v1

## 1. 作用

View Engine以 `CanonicalTrafficSample` 为唯一输入，为Business、Detection和Attack-Type三个任务构造不同、可校验且防标签泄漏的View。训练目标单独保存在View外部；推理时由流水线传入上一步产生的结构化业务先验。

## 2. 处理流程

1. `RepresentationSelector` 按任务策略选择packet、HTTP或direction sequence；没有允许的表达时返回稳定错误 `view_unavailable`。
2. `PreTokenBudgetManager` 在真实tokenizer前执行确定性字符截断并写入warning。
3. `ViewEngine` 组装任务字段、质量信息、上下文和允许的先验。
4. `ViewValidator` 立即按对应Draft 2020-12 Schema校验。
5. 训练生成器把 `view` 和 `target` 分开放入 `TaskTrainingExample`。

## 3. 三任务差异

| 任务 | 可用表达 | 先验 | 训练目标位置 |
|---|---|---|---|
| Business | packet / HTTP / direction | 禁止业务先验 | `target`，不在View内 |
| Detection | packet / HTTP | 可注入Business Adapter输出 | `target`，不在View内 |
| Attack-Type | packet / HTTP | 可注入Business Adapter输出 | `target`，不在View内 |

Attack-Type View不注入Detection的 `is_attack` 结果。`is_attack`只由推理流水线用作门控，避免成为攻击类型分类的答案提示。

## 4. 训练与推理差异

- 训练：数据集没有真实Business Adapter推理结果，因此检测和攻击类型样本的业务先验为`null`，防止用真值伪造先验。
- 推理：先调用 `build_business`；得到Business Adapter结构化输出后，再调用 `build_detection`；仅当检测结果为攻击时调用 `build_attack_type`。
- 当前View Engine只构造结构化JSON，不负责把JSON序列化成模型prompt，也不执行模型推理。

## 5. Token边界

当前执行的是tokenizer之前的确定性字符上限，并非最终token预算。统一tokenizer确定后，需要增加Serializer/Tokenizer层：固定字段顺序、模板、特殊token、真实token计数和超预算裁剪优先级。

## 6. 可重复性与验证

全量生成661,164条训练样本：Business 390,279、Detection 170,423、Attack-Type 100,462。独立校验确认66个文件、0重复、0错误，组合SHA-256为 `93af8d43eeeef338ea7a9ebcd05ab2cd9917b4c7c086759baa13c9f71d91362e`。
