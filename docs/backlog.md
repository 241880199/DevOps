# Backlog

当前进展与待办。每项标注产物与验收方式，便于他人接手时直接判断完成与否。

## 已完成

### 契约

| 项 | 产物 | 验收 |
| --- | --- | --- |
| 统一任务模型 | `contracts/task.schema.json` | 四类 job_type 可表达并有样例支撑 |
| 创建请求契约 | `contracts/job-create-request.schema.json` | `job_id` 不得由客户端携带，携带被拒绝 |
| 环境生成输入/输出 | `contracts/job-input-draft.schema.json`、`job-output-draft.schema.json` | 两层判据可表达 |
| 依赖修复输入/输出 | `contracts/job-input-repair.schema.json`、`job-output-repair.schema.json` | `finding_type` 固定 MISSING；三项验证判据齐全 |
| 全量检测输入 | `contracts/job-input-full-check.schema.json` | 基线由 commit + configuration_id 共同确定 |
| 增量检测输入 | `contracts/job-input-incremental-check.schema.json` | `baseline` 三项必填 |
| 依赖问题报告 | `contracts/error-report.schema.json` | 每条发现带位置与证据，来源可区分 |
| 产物记录 | `contracts/artifact.schema.json` | 下游可依 URI 取到内容 |
| 错误语义分层 | `contracts/error-codes.md`、`task.schema.json` 的 `allOf` | 成功/失败语义互斥 |
| 结果仅在终态交付 | `task.schema.json` 的 `allOf` | `QUEUED`/`RUNNING` 携带 `output` 或 `error` 被拒绝 |

### 实现与样例

| 项 | 产物 | 验收 |
| --- | --- | --- |
| 契约自动校验 | `scripts/validate.py` | 77/77 项通过，退出码 0 |
| 四个创建端点 | `scripts/mock_server.py` | 四类任务均 `202 → QUEUED → RUNNING → SUCCEEDED` |
| 任务查询与产物下载 | `scripts/mock_server.py` | `GET /v1/jobs/{id}`、`GET /v1/artifacts/{id}` 实测通过 |
| 基线可比性检查 | `scripts/mock_server.py` 的 `cross_checks` | 基线提交或配置不匹配时被拒绝 |
| 固定输入样例 | `fixtures/draft/` | `make` 构建、`./hello` 输出 `hello draft` |
| 12 个接口样例 | `contracts/samples/` | 覆盖四类请求、执行中、成功、两类失败、产物、报告 |

### 设计记录

| 项 | 产物 |
| --- | --- |
| 下游对接文档 | `docs/接口说明.md` |
| ADR-001 异步任务模型 | `docs/ADR/` |
| ADR-002 产物 URI 引用交接 | `docs/ADR/` |
| ADR-003 错误语义分层 | `docs/ADR/` |
| ADR-004 结果仅在终态交付 | `docs/ADR/` |
| ADR-005 依赖问题报告契约 | `docs/ADR/` |
| AI 使用与设计过程记录 | `docs/AI_USAGE.md` |

## 待办

### 需要外部条件

| 项 | 说明 | 验收 |
| --- | --- | --- |
| **构建证据采集** | `Dockerfile.ok` / `Dockerfile.broken` 的实际构建日志尚未归档——需 Docker daemon 运行 | 落盘成功日志与失败日志各一份 |
| 真实构建接入 | 当前 mock 不执行真实构建，产出为模拟值 | 用真实 `docker build` 替换模拟分支 |

### 契约待确认

| 项 | 说明 |
| --- | --- |
| 检测类任务契约 | `FULL_CHECK` / `INCREMENTAL_CHECK` 形状由我方拟出，待检测方确认或修订（见 `docs/接口说明.md` 第 10、15 节） |
| `configuration_id` 归属 | 环境生成输出当前不含该字段，需明确由谁产生、语义为何 |

### 待实现

| 项 | 说明 |
| --- | --- |
| 产物下载鉴权 | 当前下载接口无鉴权，需明确是否需要 |
| 任务持久化 | mock 为内存态，进程退出即丢失 |
| 幂等键 | 创建请求尚未支持幂等重试，需约定字段与语义 |
| 报告读取校验 | REPAIR 请求只引用报告 URI，服务端尚未实际读取并核验报告与 `repository.commit` 是否一致 |
| `GET /v1/jobs` 分页 | 任务量增长后需要 |

## 已知限制

1. **`validate.py` 与 `mock_server.py` 均依赖** `jsonschema`。选择理由见 `docs/AI_USAGE.md` 第 5 条——schema 必须作为唯一事实来源，手写第二套校验必然漂移。
2. **mock 不执行真实构建**。它是契约的可执行说明，不是可用服务；`result`、`fixed` 等字段为固定值。
3. **`FULL_CHECK` / `INCREMENTAL_CHECK` 的执行逻辑不在本仓库**，mock 对二者只做受理与状态迁移。
4. **产物引用失效未处理**。产物被清理后 URI 仍存在，接收方会遇到「引用有效但内容缺失」，当前未定义该情形下的错误码。
5. **`fixtures/draft` 的构建未在本机验证**。宿主为 Windows 且无 `make`/`gcc`，验证需在 Linux 容器中进行。

## 契约变更约定

改字段前先考虑消费者：

- **兼容变更**：新增可选字段（需同步更新共享 schema，且消费者允许未知字段）。
- **破坏变更**：字段删除、改名、改语义；`status` 或 `job_type` 枚举取值变动。

破坏变更必须递增 `schema_version`，并同步更新样例与 `validate.py`。
