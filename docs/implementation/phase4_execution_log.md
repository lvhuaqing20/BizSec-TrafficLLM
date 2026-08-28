# 阶段4实施日志：真实 Parser 与全量 Canonical 转换

## 1. 阶段目标

实现 TrafficLLM 原始 JSONL 到 `CanonicalTrafficSample` 的真实转换层，并在全量处理前完成三种输入结构、标签解析、失败审计、隐私检查和可重复性试验。

## 2. 操作记录

### 操作 1：真实格式边界审计

- 只读抽查11个数据集的train文件和阶段1审计结构。
- 确认原始记录严格为 instruction/output JSONL。
- 确认 CSIC 使用任务标记后的 HTTP JSON；CW100-2018 使用末尾方向序列；其他数据使用 TShark 字段文本。
- 发现 iscx-vpn-2016 没有 `<packet>:`，正文在换行后直接从 `frame.*` 开始；据此调整包正文边界规则。

### 操作 2：公共接口和隐私配置

- 新建 `data/` 包、`ParsedTraffic`、Parser 抽象接口和稳定错误码。
- 新建 `privacy_policy_v1.json`，冻结内部CIDR、禁止存储字段、HTTP敏感参数和payload上限。
- Parser Router 同时检查 parser ID 和返回表达类型。

### 操作 3：三个真实 Parser

- TShark Parser 使用字段名边界解析，处理时间值中的逗号；提取协议、长度、角色、方向、传输层、TLS和受控payload。
- CSIC HTTP Parser 使用 JSON decoder 和 URL parser，执行确定性敏感参数脱敏。
- Direction Parser 使用行尾锚定模式，只读取最终 Input 序列。

### 操作 4：标签解析和Canonical Builder

- LabelResolver只执行阶段1注册表中的归一化规则和精确查询。
- Builder确定性计算 sample ID 与原始行哈希。
- 成功样本组合来源、单一主表达、标签、缺失字段、warnings和隐私转换。
- 每条构造结果立即执行 Draft 2020-12 和语义校验。

### 操作 5：批量转换与失败审计

- 新建 `convert_trafficllm_dataset.py`，支持dataset/split/all、limit和显式output目录。
- 转换过程逐行流式处理，不把整个原始文件读入内存。
- 成功样本与失败记录分别写入 JSONL；单条失败不会中止其他记录。
- 失败记录只保留最小定位信息，不复制完整 instruction。

### 操作 6：自动化测试

- 新增 Parser 测试，覆盖三个格式和iscx-vpn无冒号边界。
- 新增标签归一化、Builder确定性和Schema测试。
- 新增流式集成测试，验证一条坏JSON会写入失败文件且不阻断有效记录。
- 项目测试由6项增加到13项，13/13通过。

### 操作 7：真实440条试转换

- 执行11个数据集 × train/test × 每个split前20条，共440条。
- 440条成功，0条失败；packet 360、HTTP 40、direction 40。
- ok 360、partial 80；partial来自CSIC缺Host和CW100-2024缺IP角色。
- 22个成功JSONL和22个空失败JSONL均生成文件哈希。

### 操作 8：产物级质量与隐私审计

- 440/440再次通过Schema与语义校验。
- 未发现原始 instruction key、MAC或精确IPv4进入traffic/context。
- 统计缺失字段、warnings、隐私转换和可训练任务覆盖。
- 生成 `pilot_validation_v1.json`。

### 操作 9：可重复性验证

- 使用相同输入、配置、limit执行第二次转换。
- 首次与第二次Canonical组合SHA-256均为 `43d131264dd7a9dc31f238de3b818f3c5ed454f95b59967fedebe5ec64ea00ae`。
- 生成 `pilot_determinism_v1.json`，结论 identical=true。

### 操作 10：按标签分层抽样

- 新增 `--sample-per-label`，按归一化标签和 SHA-256 稳定排序抽样，而不是依赖原文件前若干行。
- 转换1,582条，成功1,582条、失败0条；覆盖283个实际出现的注册标签。
- 发现注册表中7个CW100-2024声明标签在原数据中没有记录：`cn`、`com`、`edu`、`fr`、`gov`、`in`、`jp`；与阶段1审计一致。
- 独立Schema、语义、隐私和标签覆盖校验通过。

### 操作 11：全量转换与边界修复

- 首次全量扫描发现177条app53记录包含嵌套ICMP报文，`ip.version`形如 `4,4`。
- 经原始记录只读核查，确认它表示外层ICMP和内层原始IP包，不是非法整数。
- TShark Parser改为对多层IP、端点和TCP字段稳定选取最内层值，并记录 `multi_layer_ip_uses_innermost` warning。
- 增加嵌套IP自动化测试后重新执行全量转换。
- 544,381条全部转换成功，失败0条；453,072条为`ok`，91,309条为`partial`。
- 表达分布：packet 502,377、HTTP 34,604、direction sequence 7,400。

### 操作 12：全量产物独立校验

- 对22个Canonical文件和22个失败文件执行独立扫描。
- 记录守恒：544,381输入对应544,381成功和0失败。
- 544,381个sample ID全部唯一，重复0个。
- 三任务可用标签数：business 390,279、detection 170,423、attack type 100,462。
- 全量Canonical组合SHA-256为 `09b97848279ca2f25873f97b5531ff67716b8f44ae146a44ee9f25636fddf2da`。

## 3. 设计决定

### D1：来源配置负责路由，Parser负责内容验证

数据集映射不能使不匹配内容强行通过。每个Parser仍检查自己的结构签名。

### D2：逐行流式转换

原数据约1.4GB，转换器逐行读写，内存占用不随数据集大小线性增长。

### D3：单条失败不中止批次

预期数据错误转为稳定错误码并进入失败JSONL；程序级配置错误和缺失文件仍立即失败。

### D4：保留攻击结构，删除直接标识

HTTP只脱敏敏感参数值、邮箱和长数字，尽量保留SQL/script等攻击结构。Packet不保存精确IP、MAC和绝对时间；payload受控截断或摘要化。

### D5：Pilot结论不能外推为全量结论

该约束在执行全量前始终成立。现在全量转换和独立校验均已完成，因此全量结论以 `full_validation_v1.json` 为依据，不再由pilot外推。

### D6：嵌套网络层取最内层报文

ICMP错误等报文可能同时携带外层IP和被引用的原始IP。业务/安全任务需要被引用流量的角色，因此对逗号分隔的多层字段选取最后一个（最内层）值，并保留warning便于审计。

## 4. 阶段产物

- `src/bizsec_trafficllm/data/`真实转换组件
- `configs/canonical/privacy_policy_v1.json`
- `scripts/convert_trafficllm_dataset.py`
- `scripts/validate_converted_samples.py`
- `tests/test_traffic_parsers.py`
- `tests/test_canonical_builder.py`
- `tests/test_dataset_conversion.py`
- Pilot数据产物已在结构整理时清理；验证报告保留
- `reports/phase4/pilot_validation_v1.json`
- `reports/phase4/pilot_determinism_v1.json`
- 分层抽样数据产物已在结构整理时清理；验证报告保留
- `reports/phase4/stratified_validation_v1.json`
- `artifacts/datasets/canonical/v1/`
- `reports/phase4/full_validation_v1.json`
- `docs/data/trafficllm_conversion_v1.md`
- `reports/phase4/phase4_summary.md`

## 5. 阶段状态

阶段4完成。全量544,381条已转换并通过独立校验，原始TrafficLLM数据始终保持只读；该Canonical产物作为阶段5 View Engine的统一数据源。
