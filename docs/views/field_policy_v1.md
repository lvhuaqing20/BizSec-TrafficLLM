# View 字段策略 v1

机器可读配置：`configs/views/field_registry_v1.json`。

## 字段等级

- P0：任务成立所需，不允许 Token 裁剪；
- P1：高价值证据，预算不足时最后裁剪；
- P2：辅助信息，可先删除或截断；
- P3：高 Token 成本或低价值信息，优先处理；
- FORBIDDEN：禁止进入任何 View。

## 隐私处理

- 精确 IP 转换为 internal/external/unknown 角色；
- MAC 直接删除；
- Host/SNI/Path/Body 标记为敏感字段，进入脱敏和截断流程；
- sample_id 使用稳定哈希，不使用原始捕获路径。

## 泄漏处理

原始标签、训练目标、候选类别、原 Prompt 和数据集元信息不属于可观测网络证据，必须在 View 前删除。

真实 Host/SNI 中自然出现类别名称不自动判定为泄漏。例如真实观测到 `gmail.com` 可以保留；但来自 `output=Gmail` 的值不能复制到 Host 字段。
