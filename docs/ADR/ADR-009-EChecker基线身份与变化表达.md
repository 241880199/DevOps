# ADR-009：EChecker 使用实际图产物与环境标识基线，并返回完整报告和变化集

- **状态**：依赖检测服务已采纳，待环境生成与依赖修复服务对接确认
- **日期**：2026-09-23
- **范围**：依赖检测服务 / EChecker / E2

## Context

EChecker 比较历史状态与当前提交。历史实际依赖图只有在能够追溯到基线提交和执行环境时才可使用；同一源码提交在不同编译选项下可能产生不同依赖关系，因此只记录图的地址不足以判断结果是否可比。

增量结果同时服务两个消费者：平台需要知道相对基线新增或消除了哪些发现，MDFixer 需要当前版本仍然成立的完整缺失依赖报告。若只返回差异，修复服务必须自行重建当前完整状态；若只返回完整报告，平台无法直接解释本次提交带来的变化。

## Decision

EChecker 输入必须包含：

- `base_commit`
- `baseline.actual_graph_artifact_id`
- `baseline.commit`
- `baseline.environment_id`
- 当前 `repository.commit`
- 当前 `environment_id`

受理任务时必须满足：

- `baseline.commit` 等于 `base_commit`
- `baseline.environment_id` 等于当前 `environment_id`

`baseline.actual_graph_artifact_id` 必须能够取回 BuildChecker 产出的 `ACTUAL_GRAPH`，其 `source_commit` 与 `environment_id` 必须分别等于基线提交和基线环境。

EChecker 成功输出同时包含：

- `base_commit` 与当前 `resolved_commit`
- 当前 `environment_id`
- 当前完整错误报告引用
- `changes.added` 与 `changes.resolved`

本接口不产出新的 `ACTUAL_GRAPH`。实际图表示完整构建的实测结果，增量执行只能用 `changes` 表达相对基线的新增与消除项；需要当前完整实际图时重新运行 BuildChecker。变化项沿用错误报告发现项的核心字段，使每条新增或消除记录保留提交、位置、证据和来源。

## Alternatives

| 方案 | 优点 | 不采用的原因 |
| --- | --- | --- |
| 只返回新增和消除项 | 数据量小 | MDFixer 无法直接取得当前仍有效的完整 MD 集合 |
| 只返回当前完整报告 | 结构简单 | 调用方无法直接区分本次提交新增和消除的发现 |
| 不检查 `environment_id` | 请求字段更少 | 不同环境的图可能被误当作同一基线，比较结果不可信 |
| 输出一张增量推断的实际图 | 可直接作为下一轮图基线 | 推断图与完整构建实测图会共用 `ACTUAL_GRAPH` 类型，消费方无法区分二者 |
| 每次重新执行全量检测 | 不需要历史图 | 失去增量检测的时间优势，也无法表达历史图复用契约 |

## Consequences

- 每次增量结果都能追溯到明确的基线实际图、提交和环境。
- 输出同时包含完整状态与变化集，存在一定数据重复，但两个消费者不需要自行重建信息。
- 多轮增量通过一轮全量基线加逐轮 `changes` 串联；需要新的完整图基线时重新运行 BuildChecker。
- 服务端需要执行跨字段一致性检查；单个字段的 JSON Schema 约束不能替代这些检查。
- 样例若没有真实 EChecker 运行记录，必须标记为 `MANUAL`，不能声称来源为 `TOOL` 或提供不存在的文件访问证据。

## 相关

- `contracts/job-input-incremental-check.schema.json`
- `contracts/job-output-incremental-check.schema.json`
- `contracts/dependency-graph.schema.json`
- `contracts/samples/job.incremental-check.succeeded.json`
- `docs/interfaces/增量依赖检测.md`
