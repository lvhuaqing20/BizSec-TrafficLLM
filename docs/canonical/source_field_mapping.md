# TrafficLLM 原始字段到统一样本映射 v1

## 1. 范围

本文记录阶段3的字段映射契约。当前只定义和验证映射，不批量生成 544,381 条 CanonicalTrafficSample；真实转换器在阶段4实现。

## 2. 原始记录公共结构

实际解析到的 JSONL 结构为：

```json
{"instruction": "<任务说明 + 流量正文>", "output": "<原始标签>"}
```

公共映射：

| 原字段 | 处理 | Canonical 字段 |
|---|---|---|
| `instruction` | 仅作为解析器输入，禁止完整复制 | `traffic.representations.*` |
| `output` | 原样保存 | `labels.raw.value` |
| 归一化 output | 使用阶段1标签规则 | `labels.raw.normalized_value` |
| 标签注册表 targets | 按 dataset + normalized label 查询 | `labels.targets` |
| 文件相对路径和行号 | 确定性记录 | `source.*`、`sample_id` |

## 3. 数据集映射矩阵

以下格式来自阶段1审计和原文件样例解析：

| 数据集 | 原格式 | 统一主表达 | Parser |
|---|---|---|---|
| app53-2023 | TShark packet text | `packet` | `tshark_packet_text_v1` |
| csic-2010 | HTTP request JSON | `http_request` | `csic_http_json_v1` |
| cstnet-2023 | TShark packet text | `packet` | `tshark_packet_text_v1` |
| cw100-2018 | direction bit sequence | `direction_sequence` | `binary_direction_sequence_v1` |
| cw100-2024 | TShark packet text | `packet` | `tshark_packet_text_v1` |
| dapt-2020 | TShark packet text | `packet` | `tshark_packet_text_v1` |
| dohbrw-2020 | TShark packet text | `packet` | `tshark_packet_text_v1` |
| iscx-botnet-2014 | TShark packet text | `packet` | `tshark_packet_text_v1` |
| iscx-tor-2016 | TShark packet text | `packet` | `tshark_packet_text_v1` |
| iscx-vpn-2016 | TShark packet text | `packet` | `tshark_packet_text_v1` |
| ustc-tfc-2016 | TShark packet text | `packet` | `tshark_packet_text_v1` |

## 4. Packet 映射

| TShark 字段 | Canonical 字段 | 规则 |
|---|---|---|
| `frame.protocols` | `packet.protocols` | 按 `:` 拆分、去重并保持顺序 |
| `frame.len` | `packet.packet_length` | 非负整数 |
| `ip.version` / `ipv6.version` | `packet.network.ip_version` | 标准化为 4、6 或 null |
| `ip.src`、`ip.dst` | `src_role`、`dst_role` | 根据受控网段转为 internal/external/unknown，不保留原 IP |
| `tcp.srcport` / `udp.srcport` | `transport.src_port` | 0-65535 |
| `tcp.dstport` / `udp.dstport` | `transport.dst_port` | 0-65535 |
| `tcp.flags.*` | `transport.tcp_flags` | 只保留值为 1 的标准标志名 |
| TLS 字段 | `packet.tls` | SNI/ALPN/version；敏感值先处理 |
| payload 字段 | `packet.payload` | 记录原长度及受控 text/hex/summary，不保留无限原文 |
| MAC、精确时间、完整 IP | 不映射 | 隐私或采集环境特征 |

方向依赖部署环境提供的内部网段配置；无法判断时使用 `unknown`，不得仅凭私有 IP 之外的猜测标注方向。

## 5. HTTP 映射

CSIC parser 从 instruction 末尾的 JSON 对象提取：

| HTTP JSON 字段 | Canonical 字段 | 规则 |
|---|---|---|
| `method` | `http_request.method` | 去除首尾空白并统一为大写 |
| `url` path 部分 | `http_request.path` | 保留路径结构 |
| `url` query 部分 | `http_request.query` | 敏感值确定性脱敏 |
| `body` | `http_request.body` | 敏感值确定性脱敏，空串可标准化为 null |
| Host（原数据没有） | `http_request.host` | null，并加入 missing_fields |

URL 解析必须使用结构化 URL parser，不能通过简单的字符串切割破坏编码后的参数。

## 6. Direction Sequence 映射

CW100-2018 parser 只接受 instruction 末尾 `Input:` 或 `Input：` 后的连续二进制串：

```text
Input: ：110001... → direction_sequence.sequence = "110001..."
```

类别候选列表、任务说明和示例序列均不进入统一样本。若末尾没有唯一匹配或出现 `0/1` 之外字符，记录转换失败。

## 7. 表达识别和配置冲突

```text
数据集配置选择预期 Parser
        ↓
Parser 检查实际内容特征
        ↓
匹配 → 生成统一表达
不匹配/多重歧义 → 转换失败报告
```

配置是路由和审计依据，不是跳过内容检查的许可证。禁止把 HTTP 文本强制塞入 packet，或把任意数字串当成方向序列。

## 8. 阶段4实现边界

阶段4转换器应输出：

- 成功的 CanonicalTrafficSample JSONL；
- 失败记录报告，包含 dataset、split、相对文件、record index、错误代码；
- 每个数据集的成功/partial/失败计数；
- 可重复运行的内容哈希和配置版本。

失败报告不得复制完整敏感 payload，只保留最小定位信息和安全摘要。

