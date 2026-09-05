# 方案②：删去低支持数据集，减少统一Business标签

## 思路及实际实现

仍是一个统一Business PrefixEncoder，不是每个数据集一个Adapter，也没有预先判断数据集后选择Adapter。只把两个低支持数据集从训练和验证范围排除。

```text
7数据集 / 260标签的Messages v1
    → include-dataset筛选：保留5数据集 / 150标签
    → dataset-label-balanced采样20000条
    → 原BizSec训练接口，单GPU、batch1、GA1、20000步
    → 每1000步保存PrefixEncoder
    → 同一固定300条validation评测20个checkpoint
```

保留 `app53-2023`、`cstnet-2023`、`cw100-2024`、`iscx-tor-2016`、`iscx-vpn-2016`；排除 `cw100-2018`、`ustc-tfc-2016`。150是全局业务标签数，不能把各数据集的标签数量简单相加。

精确参数记录在 [config.json](config.json)。逻辑训练池330,504条，validation池17,727条；**20000步是采样训练，不是把训练池全量遍历一轮**。所有官方test保留；validation仍由官方train按seed42固定哈希划出5%，不是7:1:2重划分。

## 核心代码在哪里

- [训练入口](../../scripts/pilot_train_adapter.py)：重复传入 `--include-dataset`。
- [数据筛选/均衡采样](../../src/bizsec_trafficllm/training/dataset.py)：只在允许的数据集内取逻辑train或validation。
- [实际优化循环](../../src/bizsec_trafficllm/training/pilot.py)：AdamW、LR0.02、GA1；没有学习率调度器或梯度裁剪，只有有限值检测和梯度范数记录。
- [验证入口](../../scripts/evaluate_adapter_checkpoint.py)：同样筛选五个数据集，固定label-balanced的300条validation。
- [测试](../../tests/test_training_interface.py) 与 [评测测试](../../tests/test_adapter_evaluation.py)：包含筛选行为及旧默认行为的覆盖。
- [曲线汇总](scripts/summarize_large_only_curve.py)：检查新旧结果的ordered sample IDs一致后汇总；逐条结果只留服务器，不上传Git。

这一筛选能力已在提交 `140ecee` 实现。本次补充集中说明、可配置启动脚本、历史验证worker、汇总代码和聚合结果，没有再改核心训练方法。

## 如何复跑

需要仓库下的Messages v1、ChatGLM2-6B和已安装本项目的Python3.9环境。可参考 [方案①环境依赖](../paper-style/requirements.txt) 安装兼容版本；无需下载官方Stage-2源码，因为这一方案调用本仓库的训练循环。

```bash
cd /path/to/BizSec-TrafficLLM
export XM_ROOT=/path/to/xm
export BIZSEC_PYTHON="$XM_ROOT/envs/bizsec-chatglm2/bin/python3.9"
export MODEL_DIR="$XM_ROOT/models/chatglm2-6b"
export REDUCED_RUN_ROOT="$XM_ROOT/runs/large-only-review/train"
export GPU_INDEX=0

tmux new -s large-only-review
bash experiments/reduced-labels/scripts/train.sh
```

根据输出中的 `run_dir=.../business-时间戳` 设置 `TRAINING_RUN`。必须等训练结束、最终 `pilot-training-result.json` 存在后再运行下列历史评测worker；它依赖该训练元数据，不能当通用边训边评脚本。

```bash
export TRAINING_RUN=/path/to/completed/business-run
export EVALUATION_ROOT="$XM_ROOT/runs/large-only-review/validation-300"
bash experiments/reduced-labels/scripts/evaluate_large_only_worker.sh 0 1000 2000 3000 4000 5000 6000 7000 8000 9000 10000 11000 12000 13000 14000 15000 16000 17000 18000 19000 20000
```

worker对周期 `checkpoint-step-XXXXXX/pytorch_model.bin` 逐点评测；20000步用run根目录最终权重。已有评测目录会跳过，因此不要把另一轮训练混放同一 `EVALUATION_ROOT`。

重测旧20k统一模型基线时，使用同一个验证入口、`--selection-strategy label-balanced --limit 300` 和相同五个 `--include-dataset` 参数，只替换 `--checkpoint`；这样才能与缩小标签后的结果对照。完整汇总代码要求20个新checkpoint及旧基线的原始评测summary/rows文件。

## 结果与边界

| 模型 | 固定验证样本数 | Accuracy | Macro-F1 |
|---|---:|---:|---:|
| 旧7数据集模型，在保留范围内重测 | 300 | 17.33% | 14.76% |
| 新5数据集模型，最佳19000步 | 300 | 18.33% | 15.34% |
| 新5数据集模型，最终20000步 | 300 | 16.67% | 12.31% |

20个checkpoint使用同一组300条样本，且与旧基线的有序sample IDs一致。详见 [完整聚合摘要](results/checkpoint-curve-summary.json) 和 [曲线CSV](results/checkpoint-curve.csv)。机器路径已替换为 `$XM_ROOT`；指标数值保持原样。

最佳点只比同样本旧模型增加Accuracy 1.00个百分点、Macro-F1约0.58个百分点；这次实验没有显示大幅改善。APP53、CSTNET、CW100-2024仍然较弱，但**一次seed、一个小validation子集并不能证明“减少标签永远无效”**。19k是按这组validation选出的候选，不是最终test最佳模型；官方test尚未用于最终报告。

这一方案的checkpoint只保存Prefix参数和审计元数据，没有完整optimizer/scheduler/RNG恢复状态；不能与方案①的Trainer恢复文件混用。
