# TrafficLLM 数据集任务矩阵 v1

状态：Draft，等待阶段1确认。

## 1. 判定规则

- `是`：原数据提供足够语义，可以生成该任务的监督目标。
- `部分`：只有部分标签可以生成该任务目标。
- `否`：原数据不提供该任务真值，不能自动生成伪标签。
- Detection 中的 `null` 表示未知安全语义，不等于 benign。

## 2. 数据集到任务的映射

| 数据集 | 原任务 | Business | Detection | Attack-Type | 映射说明 |
|---|---|---:|---:|---:|---|
| app53-2023 | 概念漂移应用分类 | 是 | 否 | 否 | 54 个 App 标签映射为 `application` |
| csic-2010 | Web 攻击检测 | 否 | 是 | 部分 | malicious → `web_attack`，无攻击家族标签 |
| cstnet-2023 | 加密应用分类 | 是 | 否 | 否 | 20 个 App 标签映射为 `application` |
| cw100-2018 | 网站指纹识别 | 是 | 否 | 否 | 100 个网站标签映射为 `website`；清除输出末尾 `。` |
| cw100-2024 | 网站指纹识别 | 是 | 否 | 否 | 标签表 63 类，实际仅观测到 56 类 |
| dapt-2020 | APT 检测 | 否 | 是 | 部分 | APT → `apt`；normal → 非攻击 |
| dohbrw-2020 | 恶意 DoH 检测 | 否 | 是 | 部分 | malicious → `malicious_doh`；输出句式归一化 |
| iscx-botnet-2014 | Botnet 检测 | 否 | 是 | 部分 | normal → 非攻击；其余 → `botnet/<family>` |
| iscx-tor-2016 | Tor 行为分类 | 是 | 否 | 否 | 8 类映射为 `network_behavior`，Tor 不自动等于攻击 |
| iscx-vpn-2016 | VPN 应用分类 | 是 | 否 | 否 | 14 类映射为 `application`，VPN 不自动等于攻击 |
| ustc-tfc-2016 | 正常应用/恶意软件分类 | 部分 | 是 | 部分 | 10 个正常应用参与 Business；10 个恶意家族参与 Attack-Type |

## 3. USTC-TFC-2016 显式划分

### 3.1 正常应用标签

以下标签生成 Business 目标和 `is_attack=false`：

- BitTorrent
- FTP
- Facetime
- Gmail
- MySQL
- Outlook
- SMB
- Skype
- Weibo
- WorldOfWarcraft

### 3.2 恶意软件家族

以下标签生成 `is_attack=true` 和 `attack_type=malware`：

- Cridex
- Geodo
- Htbot
- Miuref
- Neris
- Nsis-ay
- Shifu
- Tinba
- Virut
- Zeus

恶意软件家族不生成 Business 监督目标，避免把攻击家族当成业务类型。

## 4. Botnet 映射

| 原标签 | Detection | Attack-Type | 审核状态 |
|---|---|---|---|
| normal | false | null | 已确定 |
| Neris | true | botnet/Neris | 已确定 |
| RBot | true | botnet/RBot | 已确定 |
| Virut | true | botnet/Virut | 已确定 |
| IRC | true | botnet/IRC | 需要确认 |

`IRC` 在当前版本依据数据集整体 Botnet 任务语义暂按攻击处理，但 IRC 协议本身不必然恶意。正式训练前应结合原数据集说明或论文定义再次确认。

## 5. 输出归一化规则

### cw100-2018

```text
gfycat.com。 → gfycat.com
```

只清除标签末尾的全角句号，不修改网站名称本身。

### dohbrw-2020

```text
The traffic category is likely to be recognized as benign.    → benign
The traffic category is likely to be recognized as malicious. → malicious
```

### 通用规则

- 去除标签首尾空白；
- 不默认改为小写；
- 不在 v1 合并跨数据集同义类别；
- 归一化后的输出必须存在于对应 `*_label.json`。

## 6. 已确认的数据异常

以下内容来自对原始文件的实际解析：

1. `cw100-2024` 标签表声明 63 类，但 train/test 只观测到 56 类；未观测类别为 `cn`、`com`、`edu`、`fr`、`gov`、`in`、`jp`。
2. `cw100-2018` 的样本标签带全角句号，而标签表不带句号。
3. `dohbrw-2020` 的样本输出是英文完整句子，而标签表使用 `benign/malicious`。
4. 原始 train/test 合计 544,381 条，经解析未发现损坏 JSON 行。

## 7. 当前数据的评估边界

- 可以分别评估三个 Adapter；
- 可以运行完整三级级联推理；
- 不能对所有样本计算完整三级真值，因为多数数据集只提供一个任务维度的标签；
- 完整端到端真值评估需要额外的多任务标注集，或明确限定在具备相应标签的子集。
