# ADR-008：BuildChecker 分别交付实际图、声明图和错误报告

- **状态**：A03 已采纳，待 B03 对接确认
- **日期**：2026-09-23
- **范围**：A03 / BuildChecker / E2

## Context

BuildChecker 需要表达三类不同信息：构建过程实际读取的依赖、构建文件声明的依赖，以及两者比较后得到的缺失依赖与冗余依赖。三类信息的用途不同：EChecker 需要复用实际依赖图，人工复核需要比较实际图与声明图，MDFixer 只消费当前仍有效的缺失依赖报告。

依赖图和报告可能较大，并且只有绑定到具体源码提交与构建配置后才具有可比性。只在任务响应中返回统计数字，无法支持复核和后续增量检测；把完整图直接内嵌进任务响应，则会使任务状态查询承载大量数据。

## Decision

BuildChecker 成功输出分别引用以下三个产物：

1. 实际依赖图 `actual_graph_artifact_id`
2. 声明依赖图 `declared_graph_artifact_id`
3. 依赖问题报告 `error_report_artifact_id`

实际图和声明图共同遵循 `contracts/dependency-graph.schema.json`，通过 `graph_kind` 区分 `ACTUAL` 与 `DECLARED`；依赖问题报告遵循 `contracts/error-report.schema.json`。

输出同时携带 `resolved_commit` 与 `configuration_id`。三个产物必须属于该提交和配置。`summary.missing` 与 `summary.redundant` 只用于快速展示数量，错误报告才是发现项的权威来源。

## Alternatives

| 方案 | 优点 | 不采用的原因 |
| --- | --- | --- |
| 只返回错误报告 | 输出最小 | EChecker 无法取得可复用的历史实际图，人工也无法复核图比较过程 |
| 合并实际图与声明图 | 只有一个图文件 | 两类边的来源和生命周期不同，消费方必须反复过滤，容易混淆实际读取与声明关系 |
| 将完整图内嵌进 Job 输出 | 一次请求即可取得全部结果 | 状态查询响应过大，图无法独立校验、下载和复用 |
| 只返回 MD/RD 数量 | 实现简单 | 无法定位依赖、提供证据或交给 MDFixer 修复 |

## Consequences

- EChecker 可以把实际图作为下一次增量检测的历史基线。
- MDFixer 可以只读取错误报告，不需要理解完整依赖图。
- 接收方需要通过约定的 artifact 读取方式额外取得文件。
- 生产方必须保证图、报告、提交和配置一致，不能把不同任务的产物组合成一次结果。
- 样例中的 artifact ID 必须存在对应的产物记录或明确标为人工契约样例，不能保留无法解析的悬空引用。

## 相关

- `contracts/dependency-graph.schema.json`
- `contracts/job-output-full-check.schema.json`
- `contracts/samples/job.full-check.succeeded.json`
- `docs/接口说明.md` 第 10 节
