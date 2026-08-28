# Business View v1

## 目标

只提供识别应用、网站或网络行为所需的可观测证据。

## 支持表示

- packet：App53、CSTNET、CW100-2024、Tor、VPN、USTC 正常应用；
- http_request：未来业务 API 识别；
- direction_sequence：CW100-2018。

## 高优先级字段

- 协议栈、目标端口；
- TLS SNI/ALPN；
- HTTP Host/Path；
- 方向与包长；
- 方向序列；
- asset_type、service_name。

## 禁止信息

- 安全规则和威胁情报；
- is_attack、attack_type、attack_family；
- 原始标签和候选类别；
- 任何上游模型 Prior。

Business View 的 `priors` 必须为空对象。
