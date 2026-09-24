# ADR-010：依赖检测服务检测接口对齐 contract-phase 公共契约

- **状态**：依赖检测服务已采用，待双方在 `docs/interfaces/` 冻结
- **日期**：2026-09-24
- **范围**：依赖检测服务 / BuildChecker / EChecker / E2
- **依据**：`241880199/DevOps` 的 `contract-phase` 分支，提交 `431a7438a2487ef505b528e7bd781b2c2563862b`

## Context

共同契约分支重新定义了 Repository、Environment、Artifact 和 Job 的公共边界。它与本地
ADR-008、ADR-009 所依据的早期字段存在破坏性差异：`environment_id` 取代
`configuration_id`，任务不再内嵌镜像和构建命令，Artifact 通过全局 ID 下载，并且共享
产出方矩阵只允许 BuildChecker 产出 `ACTUAL_GRAPH`。

旧 ADR 作为历史记录保留，不改写原文；本 ADR 记录依赖检测服务在新共同契约下采用的当前口径。

## Decision

1. FULL_CHECK / INCREMENTAL_CHECK 的 Repository 同时携带原始 `url`、规范化
   `canonical_url` 和完整 40 位 `commit`。
2. 两类检测输入只引用 `environment_id`，不内嵌 `image_ref`、项目根目录或构建命令。
3. INCREMENTAL_CHECK 通过 `baseline.actual_graph_artifact_id` 引用 BuildChecker 基线图，
   并要求基线的 commit 与 environment_id 同本次比较一致。
4. BuildChecker 产出 ACTUAL_GRAPH、DECLARED_GRAPH 和 ERROR_REPORT；EChecker 只产出
   ERROR_REPORT，并在小型 `changes` 中表达新增和消除项。
5. Artifact URI 的存储域按服务命名：`buildchecker` 或 `echecker`；内容统一通过
   `GET /v1/artifacts/{artifact_id}` 获取并核验摘要。
6. 以上是依赖检测服务可执行提案。只有写入 `docs/interfaces/` 并经双方确认后，才称为
   冻结接口。

## Consequences

- 旧的 `configuration_id`、`environment.image_ref`、`build.*`、
  `baseline.actual_graph_uri` 不再出现在依赖检测服务检测契约。
- EChecker 不再返回下一轮可复用的实际图；若双方认为增量算法必须产出该图，应先修改
  共同产出方矩阵，再同步 Schema，不能由本地实现单方面例外。
- DRAFT / REPAIR 的专有字段由环境生成与依赖修复服务维护者迁移；依赖检测服务不在本次修改中替环境生成与依赖修复服务定稿。

## 相关文件

- `contracts/job-input-full-check.schema.json`
- `contracts/job-input-incremental-check.schema.json`
- `contracts/job-output-full-check.schema.json`
- `contracts/job-output-incremental-check.schema.json`
- `contracts/dependency-graph.schema.json`
- `contracts/error-report.schema.json`
- `docs/检测侧接口提案.md`（已于 2026-09-24 并入 `docs/interfaces/全量依赖检测.md` 与 `增量依赖检测.md`，该文件已删除）
