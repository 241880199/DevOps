# Backlog

当前进展与待办。每项标注产物与验收方式，便于他人接手时直接判断完成与否。

## 已完成

| 项 | 产物 | 验收 |
| --- | --- | --- |
| 统一任务模型 | `contracts/task.schema.json` | 四类 job_type 可表达；`validate.py` 检查 01 通过 |
| DRAFT 输入契约 | `contracts/job-input-draft.schema.json` | 必填项缺失被拒绝；`validate.py` 检查 03 通过 |
| DRAFT 输出契约 | `contracts/job-output-draft.schema.json` | 迭代记录与结果结构可用；`validate.py` 检查 01 通过 |
| 产物记录契约 | `contracts/artifact.schema.json` | 下游可依 URI 取到内容；端到端验证已通过 |
| 错误语义分层 | `contracts/error-codes.md`、`task.schema.json` 的 `allOf` | 成功/失败语义互斥；`validate.py` 检查 04 通过 |
| 契约自动校验 | `scripts/validate.py` | 40/40 项通过，退出码 0 |
| 下游接口文档 | `docs/接口说明.md` | 含字段用途、联调检查清单、完整走查 |
| 错误码样例补全 | `contracts/samples/job.timed_out.exec4002.json` | `EXEC_4002` 有样例支撑 |
| 终态语义约束 | `task.schema.json` 的 `allOf` | `QUEUED`/`RUNNING` 携带 `output` 或 `error` 被拒绝 |
| 错误码与文档一致性 | `scripts/validate.py` 检查 07 | 样例用到的错误码均已归档 |
| 成功输出不变式 | `scripts/validate.py` 检查 08 | `SUCCEEDED` 蕴含各项判据为真 |
| 创建/查询接口实现 | `scripts/mock_server.py` | `202 → QUEUED → SUCCEEDED` 全流程实测通过 |
| 产物下载接口 | `scripts/mock_server.py` | `GET /v1/artifacts/{id}` 返回 539 字节实体 |
| 固定输入样例 | `fixtures/draft/` | `make` 构建、`./hello` 输出 `hello draft` |
| 架构决策记录 | `docs/ADR/ADR-001~003` | 三项决策含备选方案与代价 |
| 设计与 AI 使用记录 | `docs/AI_USAGE.md` | 逐条含采纳/修改/拒绝理由 |

## 待办

| 项 | 说明 | 验收 |
| --- | --- | --- |
| 构建证据采集 | `Dockerfile.ok` / `Dockerfile.broken` 的实际构建日志尚未归档——需 Docker daemon 运行 | 落盘成功日志与失败日志各一份 |
| 真实构建接入 | 当前 mock 不执行真实构建，产出为模拟值 | 用真实 `docker build` 替换 `run_job` 中的模拟分支 |
| 产物下载鉴权 | 当前下载接口无鉴权 | 明确是否需要，若需要则补充约定 |
| 任务持久化 | mock 为内存态，进程退出即丢失 | 落库，重启后可查询 |
| 幂等键 | 创建请求尚未支持幂等重试 | 约定幂等键字段与语义 |
| 增量检测契约 | `INCREMENTAL_CHECK` 的 input schema 未定义 | 用于验证「删除 baseline 被拒绝」 |
| 修复任务契约 | `REPAIR` 的 input schema 未定义 | 用于验证「只消费缺失依赖报告」 |

## 已知限制

1. **`validate.py` 依赖第三方库** `jsonschema`。选择理由见 `docs/AI_USAGE.md` 第 5 条——schema 必须作为唯一事实来源。若需零依赖运行，需重新评估。
2. **`mock_server.py` 不执行真实构建**。它是契约的可执行说明，不是可用服务；`output.result` 中的 `verify_stdout` 等为固定值。
3. **`GET /v1/jobs` 无分页**。任务量增长后需要补充。
4. **产物引用失效未处理**。产物被清理后 URI 仍存在，接收方会遇到「引用有效但内容缺失」，当前未定义该情形下的错误码。
5. **`fixtures/draft` 的构建未在本机验证**。宿主为 Windows 且无 `make`/`gcc`，验证需在 Linux 容器中进行。

## 契约变更约定

改字段前先考虑消费者：

- **兼容变更**：新增可选字段（需同步更新共享 schema，且消费者允许未知字段）。
- **破坏变更**：字段删除、改名、改语义；`status` 或 `job_type` 枚举取值变动。

破坏变更必须递增 `schema_version`，并同步更新样例与 `validate.py`。
