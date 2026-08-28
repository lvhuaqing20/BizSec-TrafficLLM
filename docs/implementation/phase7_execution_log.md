# 阶段七实施记录：ChatGLM2训练准备

## 2026-08-28：Phase 7A

### 目标

将已经验证的模型无关Messages Dataset接入论文实际使用的ChatGLM2-6B和P-Tuning v2训练格式，但本阶段不执行GPU训练。

### 已完成操作

1. 建立ChatGLM2-6B/P-Tuning v2模型契约与JSON Schema；
2. 建立Business、Detection、Attack-Type三份任务配置与JSON Schema；
3. 固定`pre_seq_len=128`、`prefix_projection=false`和仅PrefixEncoder可训练策略；
4. 实现Messages的严格三角色检查以及`query/response`适配；
5. 实现ChatGLM2 `build_prompt`、source/target截断、EOS、padding和answer-only label mask；
6. 实现流式读取全量Messages的真实Tokenizer长度审计脚本；
7. 建立可重复执行的训练契约校验脚本；
8. 新增4个tokenization单元测试并运行全部测试。

### 执行结果

- 训练契约校验：passed；
- 模型Schema：passed；
- 三任务配置Schema：passed；
- 任务路径一致性：passed；
- 三个checkpoint目录隔离：passed；
- 全部单元测试：27/27 passed。

验证报告：`reports/phase7/training_contract_validation_v1.json`。

### 尚未执行

真实ChatGLM2 Tokenizer全量审计未执行。当前本地Python环境没有安装
`transformers`，且尚未建立服务器锁定环境。本阶段只用Fake Tokenizer验证了
适配算法和label mask，未产生、也未声称产生真实token统计。

服务器建立环境后执行：

```bash
python scripts/audit_chatglm2_tokenizer.py \
  --messages-dir artifacts/datasets/messages/v1/examples \
  --report reports/phase7/chatglm2_token_audit_v1.json
```

### 下一步入口

1. 锁定服务器PyTorch、Transformers、CUDA环境；
2. 运行真实全量Tokenizer审计并回填最终长度；
3. 实现统一P-Tuning v2训练入口与确定性train/validation划分；
4. 用20至100条样本完成服务器GPU冒烟训练。
