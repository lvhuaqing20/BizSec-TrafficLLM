# 正式项目结构整理日志

## 1. 目标

删除早期规则型演示链路，只保留TrafficLLM真实数据处理和任务View构造；分离源码、全量数据产物与验证报告，并统一命名、依赖和文档。

## 2. 删除内容

以下规则演示代码已删除：

- `api.py`、`cli.py`；
- `pipeline.py`、`backends.py`、`fusion.py`；
- 早期Python数据结构 `schemas.py`；
- 早期简单View实现 `views.py`；
- 旧API示例；
- 4项仅验证规则演示链路的测试。

同时移除FastAPI、uvicorn和httpx依赖。JSON Schema运行时校验所需的`jsonschema`改为正式依赖。

## 3. 正式模块整理

- `src/bizsec_trafficllm/view_engine/` 更名为 `src/bizsec_trafficllm/views/`；
- 所有脚本、测试和包导出改用 `bizsec_trafficllm.views`；
- `docs/view_engine/` 合并到 `docs/views/`；
- 删除空目录、`.DS_Store`、`__pycache__`和`.pyc`；
- 依赖只由 `pyproject.toml` 管理，删除重复的`requirements.txt`。

## 4. 数据产物整理

全量数据从报告目录迁移为：

```text
artifacts/datasets/canonical/v1
artifacts/datasets/task_views/v1
```

`reports/`现在只保存小型验证JSON和Markdown总结。Pilot、分层抽样的大型中间JSONL已删除，其验证报告和阶段执行记录保留。被删除的中间数据不在回收站中，但可用冻结配置和脚本确定性重新生成。

## 5. 验证结果

- 正式单元测试17/17通过；
- Python语法检查通过；
- 阶段1、2、3契约校验全部通过；
- 544,381条Canonical重新验证通过，0失败、0重复；
- 661,164条任务样本重新验证通过，0错误、0重复；
- Canonical组合SHA-256仍为 `09b97848279ca2f25873f97b5531ff67716b8f44ae146a44ee9f25636fddf2da`；
- 任务样本组合SHA-256仍为 `93af8d43eeeef338ea7a9ebcd05ab2cd9917b4c7c086759baa13c9f71d91362e`。

因此本次整理只改变代码边界和文件位置，没有改变全量数据内容。
