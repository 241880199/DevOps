# Backlog

当前进展与待办。每项标注产物与验收方式，便于他人接手时直接判断完成与否。

## 已完成

### 契约

| 项 | 产物 | 验收 |
| --- | --- | --- |
| 统一任务模型 | `contracts/task.schema.json` | 四类 job_type 可表达并有样例支撑 |
| 创建请求契约 | `contracts/job-create-request.schema.json` | `job_id` 不得由客户端携带，携带被拒绝 |
| 环境生成输入 | `contracts/job-input-draft.schema.json` | 生成要求（仓库 + 两层判据 + 执行预算）保持稳定；DRAFT 是环境的产出方，输入不引用 `environment_id` |
| 环境生成输出 | `contracts/job-output-draft.schema.json` | 回报 `environment_id` 与 `image_artifact_id`，取代 `configuration_id` 与裸镜像 tag |
| **环境定义与查询** | `contracts/environment.schema.json`、`contracts/samples/environment.*.json` | 环境是独立对象；任务只引 `environment_id`，完整定义经 `GET /v1/environments/{environment_id}` 取 |
| 依赖修复输入/输出 | `contracts/job-input-repair.schema.json`、`job-output-repair.schema.json` | 环境按 `environment_id` 引用，报告按 `artifact_id` 取回；修复只消费 MD |
| **环境生成与依赖修复服务迁移的八条约定** | `docs/ADR/ADR-011` | 每条都有样例与可执行校验；旧结构仅存于 `task-legacy-v1` 与 ADR，无消费者 |
| 全量检测输入 | `contracts/job-input-full-check.schema.json` | 完整仓库身份 + `environment_id` |
| 增量检测输入 | `contracts/job-input-incremental-check.schema.json` | 基线以 Artifact ID、commit、environment_id 标识 |
| 全量检测输出 | `contracts/job-output-full-check.schema.json` | BuildChecker 交付两类图和错误报告 |
| 增量检测输出 | `contracts/job-output-incremental-check.schema.json` | EChecker 交付错误报告与变化集，不产出实际图 |
| 依赖图契约 | `contracts/dependency-graph.schema.json` | 实际图与声明图使用同一结构并区分 relation |
| 依赖问题报告 | `contracts/error-report.schema.json` | 每条发现带位置与证据，来源可区分 |
| 产物记录 | `contracts/artifact.schema.json` | 下游可依 URI 取到内容 |
| 错误语义分层 | `contracts/error-codes.md`、`task.schema.json` 的 `allOf` | 成功/失败语义互斥 |
| 结果仅在终态交付 | `task.schema.json` 的 `allOf` | `QUEUED`/`RUNNING` 携带 `output` 或 `error` 被拒绝 |
| **修复阶段错误码** | `contracts/error-codes.md` 第 2、6 节 | `6xxx` 段与 `REPAIR_6001`/`REPAIR_6002` 已定义并有样例 |
| **修复的成败边界** | `contracts/error-codes.md` 第 6 节、`docs/ADR/ADR-006` | 「全部候选被拒」判 `SUCCEEDED` + `fixed: []`，两条不变式有校验 |

### 实现与样例

| 项 | 产物 | 验收 |
| --- | --- | --- |
| 契约自动校验 | `scripts/validate.py` | 检查项数量以命令输出为准；退出码 0 表示全部通过 |
| 四个创建端点 | `scripts/mock_server.py` | 四类任务均 `202 → QUEUED → RUNNING → SUCCEEDED` |
| 任务查询与产物下载 | `scripts/mock_server.py` | `GET /v1/jobs/{id}`、`GET /v1/artifacts/{id}` 实测通过 |
| 基线可比性检查 | `scripts/mock_server.py` 的 `cross_checks` | 基线提交或环境不匹配时被拒绝 |
| **环境登记与查询** | `scripts/mock_server.py` | DRAFT 受理后产出 `environment_id` 并可经 `GET /v1/environments/{id}` 取到；未登记的环境使任务在受理阶段被 `400` 拒绝 |
| **交接物摘要核验** | `scripts/mock_server.py` 启动时的静态记录加载 | 产物记录与实物的摘要/大小不符即拒绝启动 |
| **报告版本检查** | `scripts/mock_server.py` 的 `cross_checks` 与执行阶段 | 声明了 `report.commit` 时在受理阶段 `400`；未声明时在执行阶段取回报告后以 `FAILED / REPAIR_6001` 收场 |
| **报告可用性检查** | `scripts/mock_server.py` 的 `report_reference_problems` | 编号取不到、产物不是 `ERROR_REPORT`、报告属于别的环境、报告不含 `MISSING` 发现——四种情形均在受理阶段被拒绝 |
| **仓库版本解析** | `scripts/mock_server.py` 的 `resolve_commit` | 缩写与缺省被解析为完整 40 位 SHA；不存在的提交使任务失败，产物记录里不会出现假提交 |
| **产物记录自检** | `scripts/mock_server.py` 的 `register_artifact` | 登记前按 `artifact.schema.json` 自检，违约即让任务失败 |
| **能力需求交接** | `scripts/mock_server.py`、`contracts/samples/create-dockerfile-job.request.json` | 环境生成请求声明所需能力（`ptrace`），环境记录据此固定；检测任务引用未声明 `ptrace` 的环境被拒绝 |
| **路径越界防护** | 两个输入 schema 的 `pattern` + `scripts/mock_server.py` | `project_subdir` 与 `makefile_path` 只接受仓库内相对路径，绝对路径与 `..` 在受理阶段被拒；环境的 `project_root` 必须落在工作区内（请求侧与产出侧各判一次） |
| **报告内容校验** | `scripts/mock_server.py` 的 `report_body` | 取回的报告按 `error-report.schema.json` 校验；内容解析不了、与产物记录不一致、无 `MISSING` 发现均被拒 |
| 固定输入样例（DRAFT） | `fixtures/draft/` | `make` 构建、`./hello` 输出 `hello E3` |
| **固定输入样例（REPAIR）** | `fixtures/mdfixer/` | 真实源码 + 人工报告 + 参考补丁；报告每条发现的 `location` 都指向磁盘上真实的规则行 |
| 接口样例 | `contracts/samples/` | 覆盖创建/受理、任务状态、各类产物记录、两个环境记录与错误报告（文件数量以目录为准） |

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
| **ADR-007 DRAFT 输出回报 `configuration_id`** | `docs/ADR/`（已被 ADR-011 取代，原文保留为历史） |
| **ADR-011 DRAFT / REPAIR 迁移到统一任务模型 2.0** | `docs/ADR/`（环境生成与依赖修复服务已采用，待双方在 `contract-phase/interfaces/` 冻结） |
| **ADR-008 BuildChecker 输出与依赖图交付** | `docs/ADR/`（依赖检测服务已采纳，待环境生成与依赖修复服务对接确认） |
| **ADR-009 EChecker 基线身份与变化表达** | `docs/ADR/`（依赖检测服务已采纳，待环境生成与依赖修复服务对接确认） |
| **ADR-010 对齐 contract-phase 公共契约** | `docs/ADR/`（取代本地依赖检测服务旧字段口径） |
| **接口交换记录** | `docs/接口交换记录.md` |
| AI 使用与设计过程记录 | `docs/AI_USAGE.md` |

> 按 `contract-phase` 工作规则，个人协作记录应放在该分支的 `records/`，每人一份；
> Git 提交历史继续承担作者、SHA 和版本追溯。当前实现分支不重复创建个人汇总文件。

## 待办

### 需要外部条件

| 项 | 说明 | 验收 |
| --- | --- | --- |
| **构建证据采集（属 E3 交付项）** | `Dockerfile.ok` / `Dockerfile.broken` / `Dockerfile.reference` 的实际构建日志尚未归档——需 Docker daemon 运行。**不属于 E2 范围**：E2 的提交材料与接口最小检查都不含构建日志，两层成功判据的实证（保留实际运行或失败记录、保存构建日志）是 E3 的交付要求。换行符一项已随本轮解决：`.gitattributes` 已固定 `fixtures/draft/**` 与 `fixtures/mdfixer/**` 为 LF，工作树与提交态字节一致 | 三份 Dockerfile 均能在 Linux 容器中按各自 README 所述构建出预期结果，并落盘成功、失败日志各一份 |
| 真实构建接入 | 当前 mock 不执行真实构建，产出为模拟值 | 用真实 `docker build` 替换模拟分支 |

### 契约待确认

| 项 | 说明 |
| --- | --- |
| 检测类任务接口冻结 | 依赖检测服务已按共享公共结构形成可执行提案，需提交 `contract-phase/interfaces/` 并由双方确认 |
| 环境生成与依赖修复服务接口冻结 | 本轮迁移后的 DRAFT / REPAIR 契约与环境查询提案同样待提交 `contract-phase/interfaces/` 确认，见 `docs/ADR/ADR-011` |
| `report.commit` 是否被采纳 | 依赖检测服务接受该可选字段并将在交付给 MDFixer 的请求侧携带 |
| `REPAIR_6001` 的两种拒绝载体 | 依赖检测服务已接受受理阶段 `400` 与执行阶段 `FAILED` 两种载体 |
| 环境定义的查询方式 | 环境生成与依赖修复服务已给出提案：`GET /v1/environments/{environment_id}`，样例与拒绝路径齐全，待双方冻结 |
| **受理阶段拒绝的错误码载体** | 共享错误码表只有描述执行期故障的 5 个码，没有「请求不合法 / 输入不可用」这一档；本地实现暂用 `EXEC_4002` 承载。同理，「环境不存在」的 `404` 也缺一档——两处都建议双方补进共享表 |

### 数据真实性

| 项 | 说明 |
| --- | --- |
| **依赖检测服务检测样例的 commit SHA** | 已使用真实提交 `d47a1523beec3311033eabf2e74c2a6ac299720d`（基线）与 `31cd2ad96ad5660db491b41aa2a396e7f1e092a3`（当前），并由校验脚本确认对象存在且前者是后者的祖先。 |
| **生成与修复侧样例的 commit SHA** | 迁移时一并替换掉此前的占位值：DRAFT 样例指向 `d47a1523…`（`fixtures/draft` 落地的那次提交），REPAIR 样例与固定 MD 报告指向 `31cd2ad9…`（`fixtures/mdfixer` 落地的那次提交）；`REPAIR_6001` 的失败样例故意指向 `d47a1523…`，与报告所依据的版本不同。检查 14 逐个核对对象存在。 |

### 待实现

| 项 | 说明 |
| --- | --- |
| 产物下载鉴权 | 当前下载接口无鉴权，需明确是否需要 |
| 任务持久化 | mock 为内存态，进程退出即丢失 |
| 幂等键 | 创建请求尚未支持幂等重试，需约定字段与语义 |
| 报告内容的深度校验 | 当前取回报告后只核对版本与「有无 `MISSING` 发现」，未逐条核对 `location` 是否指向真实文件（那一步在 E2 由固定输入样例的校验承担） |
| 报告引用失效的真实构造 | `REPAIR_6001` 已覆盖编号取不到、类型不符、环境不符、无 `MISSING`、版本不符五种情形；仍未构造「产物记录在、内容被清理」这种引用失效 |
| `GET /v1/jobs` 分页 | 任务量增长后需要 |

## 已知限制

1. **`validate.py` 与 `mock_server.py` 均依赖** `jsonschema`。选择理由见 `docs/AI_USAGE.md` 第 5 条——schema 必须作为唯一事实来源，手写第二套校验必然漂移。
2. **mock 不执行真实构建**。它是契约的可执行说明，不是可用服务；`result`、`fixed`、镜像引用内容等为固定值，环境记录里的 `runtime_capabilities` 也不会被推断或校验。
3. **`FULL_CHECK` / `INCREMENTAL_CHECK` 的 mock 只验证契约**。它会生成符合 Schema 且可下载的空图、空报告，但不运行真实 BuildChecker/EChecker，不能作为检测效果证明。
4. **产物引用失效未处理**。产物被清理后 URI 仍存在，接收方会遇到「引用有效但内容缺失」，当前未定义该情形下的错误码（修复侧由 `REPAIR_6001` 部分覆盖）。
5. **`fixtures/` 下的构建未在本机验证**。宿主为 Windows 且无 `make`/`gcc`，验证需在 Linux 容器中进行。
6. **mock 只能解析本仓库的提交**。`resolve_commit` 依据本仓库的 git 对象判断提交是否存在：外部仓库只接受完整 SHA 且无法验证其存在性，缩写与缺省一律失败。这是 mock 没有克隆远端能力的必然结果，不是契约限制。
7. **`fixtures/mdfixer/` 是 E3 的初版**。E3 还需补齐 C0/C1/C2 连续提交与四种声明风格（`TARGET` / `MACRO` / `HYBRID` / `IMPLICIT`）的参考修复对照。
8. **部分样例引用的产物编号尚无记录**。DRAFT 样例的 `iterations[].build_log_artifact_id` 与修复样例的 `verification_log_artifact_id` 指向的 `BUILD_LOG` / `VERIFY_LOG` 记录尚未提供，检查项也未覆盖——需要时再补，避免样例比契约多出未定义的东西。

## 契约变更约定

改字段前先考虑消费者：

- **兼容变更**：新增可选字段（需同步更新共享 schema，且消费者允许未知字段）。
- **破坏变更**：字段删除、改名、改语义；`status` 或 `job_type` 枚举取值变动。

破坏变更必须递增 `schema_version`，并同步更新样例与 `validate.py`。

> 历史 E2 的 DRAFT / REPAIR 样例保持 legacy `1.0`；本轮对齐 `contract-phase` 删除/改名了
> 依赖检测服务检测字段并收紧公共 Job 结构，属于破坏性变更，因此 FULL_CHECK / INCREMENTAL_CHECK
> 使用 `schema_version=2.0`。环境生成与依赖修复服务完成迁移前，不把其旧专有结构标成 2.0。
