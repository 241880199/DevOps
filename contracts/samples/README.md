# 样例索引

每个样例覆盖什么场景，以及它被哪个契约校验。样例是契约的可执行说明：字段语义看 `contracts/*.schema.json`，逐接口说明看 `docs/interfaces/`。

| 文件 | 覆盖场景 |
| --- | --- |
| `create-dockerfile-job.request.json` | DRAFT 创建请求 |
| `create-full-check-job.request.json` | FULL_CHECK 创建请求 |
| `create-incremental-check-job.request.json` | INCREMENTAL_CHECK 创建请求 |
| `create-repair-job.request.json` | REPAIR 创建请求 |
| `create-job.response.202.json` | `202` 受理响应 |
| `job.running.json` | 执行中（仅任务元数据，无 `output` 与 `error`） |
| `job.succeeded.json` | DRAFT 成功 |
| `job.full-check.succeeded.json` | FULL_CHECK 成功，引用实际图、声明图与错误报告 |
| `job.incremental-check.succeeded.json` | INCREMENTAL_CHECK 成功，返回当前报告与相对基线的变化（不产出实际图） |
| `job.repair.succeeded.json` | REPAIR 成功（采纳部分候选，拒绝一处越界改动） |
| `job.repair.no_fix.json` | REPAIR 成功但**全部候选被拒**（`fixed: []`，任务仍 `SUCCEEDED`） |
| `job.failed.env3002.json` | 失败：镜像构建失败 |
| `job.failed.analysis5001.json` | 失败：依赖分析器异常退出（`ANALYSIS_5001`） |
| `job.timed_out.exec4002.json` | 失败：任务超时 |
| `job.failed.repair6001.json` | 失败：报告版本与请求不一致（`REPAIR_6001`） |
| `artifact.json` | 产物记录：Dockerfile |
| `artifact.patch.json` | 产物记录：修复补丁（`sha256` 与 `fixtures/mdfixer/reference.patch` 一致），记录所属环境 |
| `artifact.image-ref-001.json` / `artifact.image-ref-002.json` | 产物记录：镜像引用（两个环境各一），内容与 `fixtures/draft/docker/image-ref.txt`、`fixtures/mdfixer/docker/image-ref.txt` 一致 |
| `artifact.error-report-003.json` | 产物记录：人工固定 MD 报告（内容为 `fixtures/mdfixer/error-report.json`），被修复请求样例引用 |
| `environment.draft-fixture-mode0.json` | 环境记录：`fixtures/draft` 的环境，声明 `ptrace`，被两类检测样例引用 |

校验入口：`python scripts/validate.py`（退出码 0 表示全部通过）。
