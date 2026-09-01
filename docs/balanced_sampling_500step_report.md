# Balanced-Sampling 500-Step Report

## Decision

The deterministic cross-dataset, cross-label sampler is accepted for the next
training stage. On the same fixed validation sample, it removes Attack-Type class
collapse and gives Detection non-zero attack recall. The three-Adapter architecture
does not need to change.

The resulting checkpoints are still bounded pilot artifacts, not final models.
Business remains under-trained, Detection has a high false-positive rate, and
Attack-Type still confuses botnet and malware with APT.

## Controlled change

The sequential and balanced candidates both use:

- Messages v1 train data and the same hash-held-out validation partition;
- the same deterministic PrefixEncoder initialization;
- 500 optimizer steps, gradient accumulation 1, and the configured learning rate;
- a 1024-token source budget and the same target budget;
- the same 50 validation sample IDs for each task.

The only intended training change is sample selection. The new strategy rotates over
datasets, then rotates over primary labels inside each dataset. A fixed seed hashes
dataset order, label order, and sample order inside every dataset-label group.

## Training partition audit

| Task | Train records | Datasets | Labels | Dataset-label groups |
|---|---:|---:|---:|---:|
| Business | 352,037 | 7 | 260 | 262 |
| Detection | 147,305 | 5 | 2 | 10 |
| Attack-Type | 87,685 | 5 | 5 | 5 |

The selected 500-record pilots have the following distributions:

- Business: 71–72 records per dataset and 231 represented labels;
- Detection: 100 records per dataset and 250 attack/250 benign records;
- Attack-Type: 100 records per dataset and 100 records for each of five classes.

All 500 sample IDs are unique for every task. The ordered sample-list SHA-256 digests
are `6fa1e5b2...90aae9` (Business), `63fec788...acf6` (Detection), and
`88bd01ba...393` (Attack-Type).

## Training checks

All three runs completed 500 finite optimizer steps without OOM, NaN, source
truncation, or target truncation. Peak allocated memory was about 12.9 GiB per task,
and all saved PrefixEncoder checkpoints passed reload verification.

| Task | First 50 loss mean | Last 50 loss mean | Checkpoint SHA-256 |
|---|---:|---:|---|
| Business | 1.6385 | 0.7099 | `895a6dd14ac855b3809e1a29b6783a5c484923402e9833a93a8f3c53a579d3c6` |
| Detection | 0.1787 | 0.0831 | `badbee5e36a57b97f54c5e503f3236257f9108f7f06b46e458bed98587396482` |
| Attack-Type | 0.9907 | 0.0953 | `8d4ad17c3d5270ad68a52ef972ee4ad6a27e9cdf632391f163900559e4055ce7` |

## Same-sample validation comparison

| Task | Metric | Sequential 500 | Balanced 500 |
|---|---|---:|---:|
| Business | Schema valid | 100% | 100% |
| Business | Primary accuracy | 2% | 2% |
| Business | Macro F1 | 0.0020 | 0.0022 |
| Detection | Schema valid | 100% | 100% |
| Detection | Accuracy | 50% | 60% |
| Detection | Attack recall | 0% | 92% |
| Detection | Attack F1 | 0% | 69.7% |
| Detection | Macro F1 | 33.3% | 55.4% |
| Attack-Type | Schema valid | 100% | 100% |
| Attack-Type | Accuracy | 20% | 70% |
| Attack-Type | Macro F1 | 6.7% | 69.0% |

Detection changed from predicting all 50 records as benign to TP=23, TN=7, FP=18,
and FN=2. Its attack precision is 56.1%, recall is 92.0%, and F1 is 69.7%. The
improvement is material, but specificity is only 28%, so the model still over-predicts
attacks.

Attack-Type changed from predicting all 50 records as `web_attack` to using all five
classes. Per-class correct counts are 10/10 for `web_attack`, 10/10 for
`malicious_doh`, 8/10 for `apt`, 4/10 for `botnet`, and 3/10 for `malware`.

Business produced 12 distinct labels instead of three, but only one of 50 predictions
was correct. The selected 500 records cover 231 labels, leaving only about two
examples per represented label on average. Forty-six of the 50 validation labels do
appear in the selected training records, so the remaining limitation is exposure and
learning capacity at this step count, not merely unseen labels.

## Recommended next gate

Keep the balanced sampler and use task-specific budgets instead of forcing the same
step count on all three tasks:

- Business: 5,000 steps as the next bounded gate;
- Detection: 2,500 steps, with false-positive rate and macro F1 checked every 500
  steps;
- Attack-Type: 2,500 steps, with per-class recall checked every 500 steps.

The current pilot runner materializes the v1 train partition to build a deterministic
balanced subset and saves only the final checkpoint. Before a longer formal run, add
periodic checkpoint/evaluation support; a streaming or precomputed sample-index
implementation can be added later if startup memory becomes relevant.

## Artifacts

- Balanced training: `/root/autodl-tmp/xm/runs/1024-500step-balanced`
- Balanced validation: `/root/autodl-tmp/xm/runs/balanced-validation-500step-resampled`
- Sequential reference: `/root/autodl-tmp/xm/runs/balanced-validation-500step`
