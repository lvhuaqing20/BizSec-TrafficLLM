# 阶段2汇报：任务 View 契约与字段策略

## 一、阶段结论

阶段2已完成三个 Adapter 的 View 输入规范、共享流量表示、字段注册表、Token 预算、标签泄漏策略、正反样例和自动校验器。

当前状态：**阶段2技术校验通过。**

本阶段没有转换完整 TrafficLLM 流量正文，没有生成完整训练数据，没有实现完整 View Engine，也没有训练模型。

## 二、完成内容

### 1. 三个 View Schema

- Business View：支持 packet、HTTP request、direction sequence；
- Detection View：支持 packet、HTTP request，并允许 Business Prior 或 null；
- Attack-Type View：支持 packet、HTTP request，并允许 Business Prior 或 null。

### 2. 共享结构

统一定义：

- packet 协议、方向、包长、网络角色、传输层、TLS、payload；
- HTTP method、host、path、query、body；
- 0/1 direction sequence；
- 可选窗口统计；
- Business Prior；
- parse status、missing fields 和 warnings。

### 3. 字段注册表

共注册 55 个字段规则，其中 8 个为明确禁止字段。每个字段记录：

- 路径和数据类型；
- 允许进入的 View；
- P0-P3 优先级；
- 标准化或匿名化操作；
- 敏感性与泄漏风险。

### 4. Token 预算

- 真正 Token 上限由部署模型配置提供；
- 为输出预留 64 Token；
- 按 P3 → P2 → P1 顺序确定性裁剪；
- P0 不删除；
- 仅 P0 已超限时拒绝构造，不进行字符串尾部盲截断。

### 5. 标签泄漏策略

建立 17 个禁止键和 6 个原 Prompt 内容模式。禁止：

- output、raw_label、target、ground_truth；
- candidate labels、instruction、prompt；
- confidence、evidence_codes、decision_source；
- 原论文任务提示词和候选类别列表。

真实可观测 Host/SNI 中自然出现应用名称不自动视为泄漏，校验以字段来源和 Prompt 特征为主。

### 6. 正反样例

- 4 个合法样例全部通过；
- 5 个非法样例全部被拒绝；
- 覆盖 packet、HTTP、方向序列、非法 Prior、标签泄漏、Prompt 泄漏和粒度不一致。

## 三、关键设计决策

### 1. View 与 Prompt 分离

View 只保存结构化证据。基础模型的 system prompt、Chat Template 和序列化格式由后续 Serializer 负责。

### 2. Business Prior 无 confidence

注入 Detection/Attack-Type 的 Business Prior 仅包含：

```json
{
  "business_domain": "application",
  "business_type": "web_service"
}
```

不可用时为 null。

### 3. Attack-Type 不注入 Detection Prior

Detection v1 只输出 `is_attack`；调用 Attack-Type 本身已经说明 `is_attack=true`，无需重复注入。

### 4. IP 使用角色，不使用精确地址

```text
精确 IP → internal / external / unknown
```

MAC 直接删除，避免采集环境和设备身份记忆。

### 5. 原数据没有的窗口字段保持 null

当前 TrafficLLM 单包数据不能可靠产生 QPS、PPS、基线偏离等窗口信息。Schema 允许这些字段，但转换器不得伪造。

### 6. 流量表达由原始结构与任务许可共同确定

原始样本实际结构决定能够构造 `packet`、`http_request` 或 `direction_sequence` 中的哪些表示；目标任务的 View Schema 决定允许使用哪些表示。数据集配置仅声明预期格式，实际内容不匹配时进入解析/转换失败报告，不能强制转换。

当前许可矩阵为：Business 支持三种表示；Detection 与 Attack-Type 支持 `packet` 和 `http_request`，不接收 `direction_sequence`。

## 四、验证结果

| 检查 | 结果 |
|---|---|
| View Schema 文件集合 | 3 个 View + 1 个共享定义，通过 |
| 字段注册表 | 55 个字段、无重复 ID，通过 |
| 禁止字段 | 8 个，通过 |
| Token 策略字段引用 | 通过 |
| 泄漏策略必要规则 | 通过 |
| `$ref` 目标与 JSON Pointer | 19 个引用，通过 |
| 合法样例 | 4/4 接受 |
| 非法样例 | 5/5 拒绝 |
| 阶段1回归校验 | 通过 |
| 项目单元测试 | 5/5 通过 |
| Draft 2020-12 官方校验 | 本地未安装 jsonschema；开发依赖已配置 |

## 五、阶段2产物

### Schema

- `schemas/views/shared_definitions.schema.json`
- `schemas/views/business_view.schema.json`
- `schemas/views/detection_view.schema.json`
- `schemas/views/attack_type_view.schema.json`

### 配置

- `configs/views/field_registry_v1.json`
- `configs/views/token_budget_v1.json`
- `configs/views/leakage_policy_v1.json`

### 文档

- `docs/views/common_view_contract.md`
- `docs/views/business_view_v1.md`
- `docs/views/detection_view_v1.md`
- `docs/views/attack_type_view_v1.md`
- `docs/views/field_policy_v1.md`
- `docs/views/token_budget_policy_v1.md`

### 校验

- `scripts/validate_view_contracts.py`
- `tests/fixtures/views/`
- `tests/test_view_contracts.py`
- `reports/phase2/view_contract_validation_v1.json`

## 六、下一阶段

阶段3根据三个 View 的字段需求定义 `CanonicalTrafficSample Schema`。重点是确定统一样本如何容纳三种原始表示、原始值与标准值、标签区、来源追踪、缺失字段和质量信息。

阶段3仍先设计 Schema 和样例；阶段4才开始将完整 TrafficLLM JSONL 转换为统一样本。
