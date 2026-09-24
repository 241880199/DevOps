# ADR-009：EChecker 使用提交与构建配置标识基线，并同时返回完整报告和变化集

- **状态**：依赖检测服务已采纳，待环境生成与依赖修复服务对接确认
- **日期**：2026-09-23
- **范围**：依赖检测服务 / EChecker / E2

## Context

EChecker 比较历史状态与当前提交。历史实际依赖图只有在能够追溯到基线提交和构建配置时才可使用；同一源码提交在不同编译选项下可能产生不同依赖关系，因此只记录图的 URI 不足以判断结果是否可比。

增量结果同时服务两个消费者：平台需要知道相对基线新增或消除了哪些发现，MDFixer 需要当前版本仍然成立的完整缺失依赖报告。若只返回差异，修复服务必须自行重建当前完整状态；若只返回完整报告，平台无法直接解释本次提交带来的变化。

## Decision

EChecker 输入必须包含：

- `base_commit`
- `baseline.actual_graph_uri`
- `baseline.commit`
- `baseline.configuration_id`
- 当前 `repository.commit`
- 当前 `environment.configuration_id`

受理任务时必须满足：

- `baseline.commit` 等于 `base_commit`
- `baseline.configuration_id` 等于当前 `environment.configuration_id`

EChecker 成功输出同时包含：

- `base_commit` 与当前 `resolved_commit`
- 当前 `configuration_id`
- 更新后的实际依赖图引用
- 当前完整错误报告引用
- `changes.added` 与 `changes.resolved`

更新后的实际依赖图可作为下一次增量检测的基线。变化项沿用错误报告发现项的核心字段，使每条新增或消除记录保留提交、位置、证据和来源。

## Alternatives

| 方案 | 优点 | 不采用的原因 |
| --- | --- | --- |
| 只返回新增和消除项 | 数据量小 | MDFixer 无法直接取得当前仍有效的完整 MD 集合 |
| 只返回当前完整报告 | 结构简单 | 调用方无法直接区分本次提交新增和消除的发现 |
| 不检查 `configuration_id` | 请求字段更少 | 不同构建配置的图可能被误当作同一基线，比较结果不可信 |
| 每次重新执行全量检测 | 不需要历史图 | 失去增量检测的时间优势，也无法表达历史图复用契约 |

## Consequences

- 每次增量结果都能追溯到明确的基线提交和构建配置。
- 输出同时包含完整状态与变化集，存在一定数据重复，但两个消费者不需要自行重建信息。
- 服务端需要执行跨字段一致性检查；单个字段的 JSON Schema 约束不能替代这些检查。
- 样例若没有真实 EChecker 运行记录，必须标记为 `MANUAL`，不能声称来源为 `TOOL` 或提供不存在的文件访问证据。

## 相关

- `contracts/job-input-incremental-check.schema.json`
- `contracts/job-output-incremental-check.schema.json`
- `contracts/dependency-graph.schema.json`
- `contracts/samples/job.incremental-check.succeeded.json`
- `docs/接口说明.md` 第 10 节
