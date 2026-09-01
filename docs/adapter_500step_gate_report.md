# 500-Step Adapter Go/No-Go Report

## Scope

This run checks whether the existing three-Adapter architecture is ready for a longer
training run. It does not use the final test split and does not claim final model
quality. All inputs come from Messages v1.

The candidate checkpoints use a 1024-token source budget and 500 optimizer steps with
gradient accumulation set to 1 for this bounded gate. They are compared with the
existing 100-step, 256-token-source checkpoints. This is a baseline-to-candidate
comparison, not a controlled ablation of step count alone.

## Reproducible validation protocol

`scripts/evaluate_adapter_checkpoint.py` reads the existing hash-held-out validation
partition, groups records by the task's primary label, ranks labels and samples with a
fixed seed, and selects records by deterministic label round-robin. The same 50 sample
IDs are therefore used for both checkpoints of a task while avoiding file-order bias.

The evaluator reports JSON Schema validity, exact-output accuracy, primary-label
accuracy, macro F1, truncation, and Detection confusion counts. Raw rows and summaries
are saved outside the Git repository under `/root/autodl-tmp/xm/runs`.

## Training gate results

All three 1024-token preflight runs and all three 500-step runs completed without OOM
or NaN. Peak allocated GPU memory was about 12.9 GiB per task, all checkpoints were
reload-verified, and the final training losses were 0.3696 (Business), 0.2100
(Detection), and 0.000192 (Attack-Type).

| Task | Candidate checkpoint SHA-256 | Wall time |
|---|---|---:|
| Business | `29e46b963ee2a3a66448cc79b9d1058bbb701ebf524fe7e618c7a91442236e19` | 245 s |
| Detection | `35dac2977928a87510ac0693a450d54d56aafc6087d7636b66b01dc89fa4f852` | 242 s |
| Attack-Type | `f672b77f97dfcf2db9e61a7a450422432c5c091ea88110bc8e1b6f4440f8f0c3` | 253 s |

## Fixed balanced validation results

| Task | Checkpoint | Schema valid | Primary accuracy | Macro F1 | Source truncated |
|---|---:|---:|---:|---:|---:|
| Business | 100-step baseline | 56% | 0% | 0.0000 | 50/50 |
| Business | 500-step candidate | 100% | 2% | 0.0020 | 0/50 |
| Detection | 100-step baseline | 98% | 48% | 0.3243 | 50/50 |
| Detection | 500-step candidate | 100% | 50% | 0.3333 | 0/50 |
| Attack-Type | 100-step baseline | 56% | 20% | 0.1053 | 50/50 |
| Attack-Type | 500-step candidate | 100% | 20% | 0.0667 | 0/50 |

The balanced Detection set contains 25 attack and 25 benign records. The candidate
predicted all 50 as benign, giving TP=0, TN=25, FP=0, FN=25, and attack recall/F1=0.

The balanced Attack-Type set contains 10 records from each of five classes: `apt`,
`botnet`, `malicious_doh`, `malware`, and `web_attack`. The candidate predicted all 50
as `web_attack`.

The balanced Business set contains 50 distinct labels. The candidate produced valid
JSON for every record but used only three output labels; 49 of 50 labels were wrong.

## Cause found in the bounded training input

The current iterator streams files in deterministic dataset-name order. The bounded
pilot consumes the first records without cross-dataset shuffling:

- the first 500 Business records all come from `app53-2023`;
- the first 500 Detection records all come from `csic-2010`;
- the first 500 Attack-Type records all come from `csic-2010` and are all
  `web_attack`.

The full Attack-Type training partition has 87,685 records across five labels, but
their first positions in the current stream are 1 (`web_attack`), 11,375 (`apt`),
15,889 (`malicious_doh`), 38,414 (`botnet`), and 56,500 (`malware`). Simply extending
the present bounded run therefore spends many early updates on one dataset and one
attack class.

## Gate decision

**Engineering Go, model-quality No-Go.** Model loading, 1024-token forward/backward,
checkpoint save/reload, inference, Schema output, and fixed evaluation all work. The
500-step candidate also removes source truncation and stabilizes JSON output. However,
the three classifiers are not ready for a formal longer run with the current
file-order sampler.

The next step is to keep the three-Adapter architecture unchanged while replacing the
bounded sequential sampler with a deterministic cross-dataset/class-aware training
sampler. After that change, repeat a short gate on the same fixed balanced validation
sample before authorizing a long run.
