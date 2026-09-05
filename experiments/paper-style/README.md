# 方案①：用 TrafficLLM 的训练实现训练 BizSec Business

## 实际实现，不是完整论文复现

输入仍是 **CSTNET-2023 的 BizSec 单包 Business View**，输出仍是
`{"business_domain":"application","business_type":"类别"}`。
采用官方 TrafficLLM **Stage-2 Trainer** 训练 ChatGLM2-6B 的独立 PrefixEncoder。
这里没有 Stage-1 任务理解训练、Flow重构或多任务路由，也不是使用论文原始输入/数据的端到端复现。

```text
Messages v1 / CSTNET / Business
    → 保留 system + "\n\nTraffic view:\n" + user 与 assistant JSON
    → 按类别稳定哈希重划分 8:1:1
    → 官方 Stage-2 Trainer + ChatGLM2-6B + PrefixEncoder
    → 保存 Adapter、optimizer、scheduler、Trainer及RNG状态
    → 固定400条 validation生成JSON → Accuracy / Macro-F1 / 格式合法率
```

## 数据与可比性

- 20类；train 78,164、validation 9,770、test 9,773；具体数量和文件摘要在 [split-manifest.json](results/split-manifest.json)。
- 转换脚本将原 Messages 的 train 与 test 合并，再按类别、seed=42稳定哈希重建8:1:1；**它没有保留原官方test边界**。这里只能称重划分实验，不能当官方test成绩。
- sample_id跨集合交集为0，但这不是Flow/Session分组划分，也不能证明底层流量无关联泄漏。
- 固定验证取新validation中每类前20条，共400条。没有用新test挑checkpoint；最终test尚未报告。
- 旧4500步模型也在这400条上测过，但旧模型的训练划分不同，新validation可能包含旧模型见过的记录。因此旧模型结果仅作参考，不能作为严格无泄漏、单变量对照。
- 输入是单包结构化字段，不等于论文的Flow输入；该CSTNET Business View的payload/TLS内容缺失问题没有在本方案中修复。

## 实际训练历程

共同配置：ChatGLM2-6B、Prefix长度128、只训练1,835,008个PrefixEncoder参数、LR起点0.02、source/target上限1024/32、seed=42。记录的Tokenizer审计最长source608、target16，采用1024/32没有截断。

| 运行脚本 | 步数含义 | GPU × 每卡batch × 累积 | 学习率 | 保存周期 |
|---|---|---|---|---|
| `train_short_200.sh` | 从零到200，单独校准 | 3 × 1 × 5 = 15 | linear，200步衰减到0 | 100 |
| `train_screen_1000_constant.sh` | 另从零到1000 | 3 × 1 × 5 = 15 | constant 0.02 | 250 |
| `resume_1000_to_2000_constant.sh` | 接上一步，到全局2000 | 3 × 1 × 5 = 15 | constant 0.02 | 250 |
| `resume_2000_to_20000_linear.sh` | 恢复2000，目标全局20000 | 3 × 1 × 5 = 15 | 改为linear，按20000步计划 | 1000 |
| `resume_6000_to_20000_single_gpu.sh` | 恢复6000，单GPU继续 | 1 × 1 × 16 = 16 | 保持20000步linear计划 | 1000 |
| `resume_6000_to_10000_gate_single_gpu.sh` | 上述续训，但10000处外部门控停止 | 1 × 1 × 16 = 16 | 仍按20000步计划，不是10000步衰减 | 1000 |

不要把表中各行全部当成从零独立训练。恢复脚本需要已有的完整checkpoint，而不仅是Prefix权重。三卡改一张卡改变了有效batch和数据迭代/RNG条件，不保证与三卡连续训练逐位等价。

官方 `trafficllm_stage2.sh` 的代码示例是1卡×batch1×累积16、20000步、LR0.02、Prefix128、source/target1024/32、每4000步保存。这里早期constant LR、三卡有效batch15、保存频率、数据划分和JSON目标均是实际差异，因此称“论文式训练对照”，不声称参数/数据完全一致。

10000步门控是历史外部进程停止脚本，不是Trainer内置early stopping；它检查权重、optimizer、scheduler及trainer_state后发信号，可能多执行少量后续计算，且没有逐项验证RNG文件。若继续恢复，需先确认完整状态文件。发布这份脚本不表示本次执行或验证了10000步训练。

## 环境与官方源码

实际环境：Linux、Python3.9、PyTorch2.0.1+cu118、Transformers4.30.2、Accelerate0.20.3；依赖见 [requirements.txt](requirements.txt)，历史完整冻结记录见 [environment-freeze.txt](environment-freeze.txt)。冻结记录已去掉本机editable安装地址；它是证据，不建议不加区分地安装其中所有工具。

官方代码：[ZGC-LLM-Safety/TrafficLLM](https://github.com/ZGC-LLM-Safety/TrafficLLM)，固定提交
`95b88f7357dbdd24873be9744e223c9dbf193007`。阅读路径：`dual-stage-tuning/main.py`、`trainer.py`、`trainer_seq2seq.py` 和 `trafficllm_stage2.sh`。

[兼容补丁](patches/0001-runtime-portability.patch)只有两处：允许外部指定可见GPU；缩短样例日志并先过滤label mask再解码。它不改变tokenization、loss、优化器或采样算法。上游文件保留其原版权/许可；此仓库不重新授权或捆绑分发完整上游代码、数据和模型。

以下命令仅在你决定复跑时执行；安装/训练会使用网络、磁盘或GPU：

```bash
cd /path/to/BizSec-TrafficLLM
export XM_ROOT=/path/to/xm
export PAPER_RUN_ROOT="$XM_ROOT/paper-review-run"
export TRAFFICLLM_ROOT="$XM_ROOT/trafficllm-review-runtime"
export PAPER_ENV="$TRAFFICLLM_ROOT/environment/trafficllm-py39"
export MODEL_DIR="$XM_ROOT/models/chatglm2-6b"

python3.9 -m venv "$PAPER_ENV"
"$PAPER_ENV/bin/python" -m pip install 'torch==2.0.1+cu118' --index-url https://download.pytorch.org/whl/cu118
"$PAPER_ENV/bin/python" -m pip install -r experiments/paper-style/requirements.txt
"$PAPER_ENV/bin/python" -m pip install -e '.[dev]'
bash experiments/paper-style/scripts/setup_upstream.sh
```

若已有经过核对的环境和官方checkout，设置 `PAPER_ENV` 和 `TRAFFICLLM_CODE=/path/to/TrafficLLM/dual-stage-tuning` 后跳过重建。`setup_upstream.sh`拒绝覆盖已有checkout。模型目录需要ChatGLM2原生Tokenizer和模型Python代码（`trust_remote_code`），只加载你信任的模型来源。

## 数据准备、训练和验证

先准备本仓库数据管线生成的Messages v1文件；这些文件不在Git中。数据转换示例会重建划分，勿与保留官方test的实验混用。

```bash
"$PAPER_ENV/bin/python" experiments/paper-style/scripts/prepare_bizsec_cstnet_paper_data.py \
  --train artifacts/datasets/messages/v1/examples/business/cstnet-2023/train.jsonl \
  --test artifacts/datasets/messages/v1/examples/business/cstnet-2023/test.jsonl \
  --output-dir "$PAPER_RUN_ROOT/data/cstnet-business-8-1-1" --seed 42

# 三GPU历史1000步筛选；仅有一张卡时不要直接运行此脚本。
tmux new -s paper-review
bash experiments/paper-style/scripts/train_screen_1000_constant.sh
# Ctrl+B，松开后按D：离开tmux但保留训练。
```

环境变量应在进入tmux前导出。200步校准单独可选；后续按训练历程表选择恢复脚本。两个从零脚本拒绝覆盖非空输出目录；恢复脚本只应指向你明确希望继续的实验。

```bash
CUDA_VISIBLE_DEVICES=0 "$PAPER_ENV/bin/python" experiments/paper-style/scripts/evaluate_business_checkpoint.py \
  --model "$MODEL_DIR" \
  --checkpoint "$PAPER_RUN_ROOT/runs/screen-1000-constant-ddp3-ga5/checkpoint-1000" \
  --data "$PAPER_RUN_ROOT/data/cstnet-business-8-1-1/bizsec_cstnet_business_validation.json" \
  --output-dir "$PAPER_RUN_ROOT/evaluations/screen-1000-validation400" \
  --per-label 20 --seed 42 --top-p 0.90 --temperature 0.10
```

验证脚本按生成的 `business_type` 计分，并单独记录严格两字段JSON合法率。seed固定仍不保证不同GPU/运行环境的随机生成逐位相同。只加载自己生成或可信来源的PyTorch checkpoint。

## 已有结果（不是本次新跑）

| 节点 | Accuracy | Macro-F1 |
|---|---:|---:|
| 200步linear校准 | 5.25% | 1.46% |
| 1000步constant | 17.25% | 13.71% |
| 1750步constant | 36.25% | 34.51% |
| 2000步constant | 35.00% | 34.31% |
| 3000步linear续训 | 50.50% | 49.62% |
| 4000步 | 53.25% | 53.59% |
| 5000步 | 56.25% | 54.62% |
| 6000步 | 62.50% | 62.05% |

完整15份聚合指标在 [results](results)，含20类混淆矩阵和分类报告，没有逐条流量或预测。机器绝对路径替换成 `$XM_ROOT`，数值未改动。当前证据说明训练后分类能力有所改善，**不能认定论文方法无效，也不能当作正式完整测试结果**。本次没有检查正在训练的进度，不提供未经核实的10000步指标。
