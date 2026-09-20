# Backlog

当前进展与待办。每项标注产物与验收方式，便于他人接手时直接判断完成与否。

## 已完成

### 契约

| 项 | 产物 | 验收 |
| --- | --- | --- |
| 统一任务模型 | `contracts/task.schema.json` | 四类 job_type 可表达并有样例支撑 |
| 创建请求契约 | `contracts/job-create-request.schema.json` | `job_id` 不得由客户端携带，携带被拒绝 |
| 环境生成输入/输出 | `contracts/job-input-draft.schema.json`、`job-output-draft.schema.json` | 两层判据可表达；`project_subdir` 可指定项目根；输出回报 `configuration_id` |
| 依赖修复输入/输出 | `contracts/job-input-repair.schema.json`、`job-output-repair.schema.json` | `finding_type` 固定 MISSING；三项验证判据齐全；`report.commit` 使版本校验前移 |
| 全量检测输入 | `contracts/job-input-full-check.schema.json` | 基线由 commit + configuration_id 共同确定 |
| 增量检测输入 | `contracts/job-input-incremental-check.schema.json` | `baseline` 三项必填 |
| 依赖问题报告 | `contracts/error-report.schema.json` | 每条发现带位置与证据，来源可区分 |
| 产物记录 | `contracts/artifact.schema.json` | 下游可依 URI 取到内容 |
| 错误语义分层 | `contracts/error-codes.md`、`task.schema.json` 的 `allOf` | 成功/失败语义互斥 |
| 结果仅在终态交付 | `task.schema.json` 的 `allOf` | `QUEUED`/`RUNNING` 携带 `output` 或 `error` 被拒绝 |
| **修复阶段错误码** | `contracts/error-codes.md` 第 2、6 节 | `6xxx` 段与 `REPAIR_6001`/`REPAIR_6002` 已定义并有样例 |
| **修复的成败边界** | `contracts/error-codes.md` 第 6 节、`docs/ADR/ADR-006` | 「全部候选被拒」判 `SUCCEEDED` + `fixed: []`，两条不变式有校验 |

### 实现与样例

| 项 | 产物 | 验收 |
| --- | --- | --- |
| 契约自动校验 | `scripts/validate.py` | 110/110 项通过，退出码 0 |
| 四个创建端点 | `scripts/mock_server.py` | 四类任务均 `202 → QUEUED → RUNNING → SUCCEEDED` |
| 任务查询与产物下载 | `scripts/mock_server.py` | `GET /v1/jobs/{id}`、`GET /v1/artifacts/{id}` 实测通过 |
| 基线可比性检查 | `scripts/mock_server.py` 的 `cross_checks` | 基线提交或配置不匹配时被拒绝 |
| **报告版本检查** | `scripts/mock_server.py` 的 `cross_checks` | `report.commit` 与 `repository.commit` 不一致时以 `400` 拒绝 |
| 固定输入样例（DRAFT） | `fixtures/draft/` | `make` 构建、`./hello` 输出 `hello draft` |
| **固定输入样例（REPAIR）** | `fixtures/mdfixer/` | 真实源码 + 人工报告 + 参考补丁；报告每条发现的 `location` 都指向磁盘上真实的规则行 |
| 接口样例 | `contracts/samples/` | 15 个文件，覆盖四类请求、执行中、三类成功、三类失败、两类产物、报告 |

### 设计记录

| 项 | 产物 |
| --- | --- |
| 下游对接文档 | `docs/接口说明.md` |
| ADR-001 异步任务模型 | `docs/ADR/` |
| ADR-002 产物 URI 引用交接 | `docs/ADR/` |
| ADR-003 错误语义分层 | `docs/ADR/` |
| ADR-004 结果仅在终态交付 | `docs/ADR/` |
| ADR-005 依赖问题报告契约 | `docs/ADR/` |
| **ADR-006 修复失败边界与报告版本一致性** | `docs/ADR/`（已采纳） |
| **ADR-007 DRAFT 输出回报 `configuration_id`** | `docs/ADR/`（提案，待 A 组确认） |
| **配对组接口交换记录** | `docs/配对组接口交换记录.md` |
| AI 使用与设计过程记录 | `docs/AI_USAGE.md` |

> 个人贡献与版本的可追溯性由 **git 提交历史**承担（作者、提交 SHA、提交说明），
> 不再另存一份汇总文件——那样会与 git 记录形成两份会各自演化的事实。
> 每次改动的说明、验证结果与提交建议见 `logs/` 下的改动日志。

## 待办

### 需要外部条件

| 项 | 说明 | 验收 |
| --- | --- | --- |
| **构建证据采集** | `Dockerfile.ok` / `Dockerfile.broken` / `Dockerfile.reference` 的实际构建日志尚未归档——需 Docker daemon 运行 | 落盘成功日志与失败日志各一份 |
| **换行符核验** | `fixtures/draft/docker/*` 在工作树中为 **CRLF**（`Dockerfile.reference` 为 LF）。宿主为 Windows，需在 Linux 容器中确认 `RUN` 的 `\` 续行仍被正确解析 | 三份 Dockerfile 均能在容器中按 README 所述构建出预期结果 |
| 真实构建接入 | 当前 mock 不执行真实构建，产出为模拟值 | 用真实 `docker build` 替换模拟分支 |

### 契约待确认

| 项 | 说明 |
| --- | --- |
| 检测类任务契约 | `FULL_CHECK` / `INCREMENTAL_CHECK` 形状由我方拟出，待检测方确认或修订（见 `docs/接口说明.md` 第 10、15 节） |
| `configuration_id` 语义与粒度 | 已提案由 DRAFT 回报（ADR-007），待检测方确认；取值粒度「凡影响依赖关系的都算在内」需双方细化 |
| `report.commit` 是否被采纳 | 已作为可选字段实现，待检测方确认愿意在请求侧带上该值 |
| `REPAIR_6001` 的两种拒绝载体 | 受理阶段 `400` 与执行阶段 `FAILED`，需确认可接受 |
| 产物 URI 的解析方式 | `ADR-002` 给了本地映射与下载接口两种，**尚未选定一种**（见 `docs/接口说明.md` 第 9 节） |

### 数据真实性

| 项 | 说明 |
| --- | --- |
| **样例中的 commit SHA 是占位值** | 样例统一使用 `b06e66ec9f3d2a1b4c5e6f708192a3b4c5d6e7f8`，由短 SHA `b06e66e` 补全而成，**未经 `git rev-parse` 核对**。它看起来是一个合法的完整 SHA——正是 `AI_USAGE.md` 第 2 条点名的「看起来合理但无意义」那类数值。替换：`git rev-parse HEAD` 取真实值，同步替换 `contracts/samples/*.json` 与 `fixtures/mdfixer/error-report.json` 中的全部出现（`scripts/validate.py` 检查 11 会核验各处一致，替换后重跑即可） |

### 待实现

| 项 | 说明 |
| --- | --- |
| 产物下载鉴权 | 当前下载接口无鉴权，需明确是否需要 |
| 任务持久化 | mock 为内存态，进程退出即丢失 |
| 幂等键 | 创建请求尚未支持幂等重试，需约定字段与语义 |
| **报告读取与核验** | `report.commit` 未声明时，服务端应取回报告并校验其 `repository.commit`；mock 尚未实现取回逻辑，只在声明了 `report.commit` 时做受理检查 |
| 报告引用失效 | `REPAIR_6001` 覆盖「报告取不到」，但 mock 未实际构造该场景 |
| `GET /v1/jobs` 分页 | 任务量增长后需要 |

## 已知限制

1. **`validate.py` 与 `mock_server.py` 均依赖** `jsonschema`。选择理由见 `docs/AI_USAGE.md` 第 5 条——schema 必须作为唯一事实来源，手写第二套校验必然漂移。
2. **mock 不执行真实构建**。它是契约的可执行说明，不是可用服务；`result`、`fixed`、`configuration_id` 等字段为固定值。
3. **`FULL_CHECK` / `INCREMENTAL_CHECK` 的执行逻辑不在本仓库**，mock 对二者只做受理与状态迁移。
4. **产物引用失效未处理**。产物被清理后 URI 仍存在，接收方会遇到「引用有效但内容缺失」，当前未定义该情形下的错误码（修复侧由 `REPAIR_6001` 部分覆盖）。
5. **`fixtures/` 下的构建未在本机验证**。宿主为 Windows 且无 `make`/`gcc`，验证需在 Linux 容器中进行。
6. **`fixtures/mdfixer/` 是 E3 的初版**。E3 还需补齐 C0/C1/C2 连续提交与四种声明风格（`TARGET` / `MACRO` / `HYBRID` / `IMPLICIT`）的参考修复对照。

## 契约变更约定

改字段前先考虑消费者：

- **兼容变更**：新增可选字段（需同步更新共享 schema，且消费者允许未知字段）。
- **破坏变更**：字段删除、改名、改语义；`status` 或 `job_type` 枚举取值变动。

破坏变更必须递增 `schema_version`，并同步更新样例与 `validate.py`。

> **E2 阶段的三处改动都是兼容变更**（新增可选字段）：`report.commit`、`project_subdir`、
> `output.configuration_id`。因此 `schema_version` 保持 `1.0` 不变。若其中任一项后续
> 改为必填，则必须递增版本并双方同步升级。
