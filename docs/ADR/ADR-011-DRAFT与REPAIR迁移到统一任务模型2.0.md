# ADR-011：DRAFT / REPAIR 迁移到统一任务模型 2.0

- **状态**：环境生成与依赖修复服务已采用，待双方在 `contract-phase/interfaces/` 冻结
- **日期**：2026-09-24
- **范围**：环境生成与依赖修复服务 / DRAFT / REPAIR / E2
- **取代**：`docs/ADR/ADR-007` 中与环境身份相关的部分（`configuration_id`）
- **依据**：`241880199/DevOps` 的 `contract-phase` 分支 `431a7438a2487ef505b528e7bd781b2c2563862b`；依赖检测服务侧先例见 `docs/ADR/ADR-010`

## Context

共同契约把环境定义成**独立的业务对象**：平台全局唯一的 `environment_id`，由环境的产出方保存并提供；任务只按 ID 引用，不内嵌定义；**构建命令只在环境里表达**；`configuration_id` 不再单独存在。

环境生成与依赖修复服务侧的 DRAFT 与 REPAIR 此前仍停在本地 1.0 结构：DRAFT 用 `configuration_id` 表达构建配置、用裸镜像 tag（`draft-fixture-ok:1.0`）表达镜像；REPAIR 在输入里内嵌 `environment{image_ref, build_command, verify_command, recheck_command}` 与 `project_subdir`。后果有两类：

1. **环境身份没有生产者。** 检测方要在输入里填 `configuration_id`，却只能自己发明——这正是依赖检测服务在 ADR-007 阶段提出的问题；共同契约用 `environment_id` 回答了它，而产出方（DRAFT）尚未履约，依赖检测服务的检测样例只能引用一个悬空的 `env-draft-fixture-mode0`。
2. **同一约定存在多份副本。** 构建命令同时出现在 DRAFT 输出、REPAIR 输入的 `environment` 段与检测侧的输入结构里，三处各自演化后「同一个环境」会有三种说法。

## Decision

1. **DRAFT 是环境的产出方**：构建成功后回报 `environment_id`，完整 Environment 记录由本服务保存并提供。
2. **环境定义的查询方式**：新增 `GET /v1/environments/{environment_id}`，返回 `contracts/environment.schema.json` 定义的对象——`image` 按 `artifact_id` 引用 `IMAGE_REF` 产物、`project_root` 是**容器内**位置、`build_command`、`runtime_capabilities`。
3. **DRAFT 输出对齐 2.0**：`configuration_id` → `environment_id`；`image_ref`（裸 tag）→ `image_artifact_id`（`IMAGE_REF` 产物编号）。
4. **DRAFT 输入保持不变**：它是环境的**产生方**而不是引用方——环境此时还不存在。输入表达「生成要求」（`repository` + `build{command, verify_command, verify_expect, project_subdir}` + `limits`），其中 `build.command` 原样成为 `Environment.build_command`，`build.project_subdir`（仓库内路径）决定 `Environment.project_root`（容器内路径）。
5. **REPAIR 输入对齐 2.0**：`environment{...}` 整段 → `environment_id`；新增 `verification{verify_command, recheck_command?}`；删除 `project_subdir`，报告与 `makefile_path` 的路径基准改取 `environment.project_root`。
6. **报告的引用方式**：`report.artifact_id` 必填，是唯一的取回依据（`GET /v1/artifacts/{artifact_id}`）；`report.artifact_uri` 保留为**可选对照字段**，填了必须与产物记录里的 `uri` 一致。
7. **产物归属环境**：环境生成服务自己的产物（`DOCKERFILE` / `BUILD_LOG` / `VERIFY_LOG` / `IMAGE_REF`）`environment_id` 一律为 `null`——产出时环境尚不存在；检测与修复的产物必须记录所属环境。
8. **版本**：四类任务统一 `schema_version=2.0`；旧结构只作为历史保留在 `contracts/task-legacy-v1.schema.json` 与本文档，不再有任何样例引用。
9. **存储域按服务命名**：`draft` / `mdfixer`（此前样例与 mock 各自用了不同的域、且都不是服务名，本轮一并纠正）。

## Alternatives

| 方案 | 取舍 |
| --- | --- |
| DRAFT 输入也改成与 Environment 同形（`project_root` / `working_directory` / `build_command` / `runtime_capabilities`） | 字段名一套到底，但**项目根**在「仓库内」与「容器内」是两种语义；同名字段装两种含义会埋歧义，且 DRAFT 的输入是生成要求、不是已有环境 |
| 环境定义内嵌在 DRAFT 输出里，下游不再查询 | 少一个端点，但违反共享契约「任务只引 ID」；环境变更后各处副本无法同步 |
| 把验证命令并入 Environment（环境承载「怎么构建、怎么验证」） | 语义更集中，但要改共享文档、超出环境生成与依赖修复服务的权限；且复检是修复流程的判据，不是环境的属性 |
| REPAIR 只留 `artifact_uri`（照参考页字面写 URI） | 与共享契约的全局 ID 下载相左；URI 暴露存储位置，换存储即失效 |
| 环境定义就地修改（同一个 `environment_id` 换内容） | 少一次新建，但下游手里的同一 ID 会指向两种环境，「按 ID 对齐基线」随即失效 |

## Consequences

- **正面**：环境身份有了唯一的生产者与唯一的查询入口；构建方式不再有多份副本；检测与修复的基线对齐从「比字符串」变成「比环境身份」。
- **代价**：破坏性变更——REPAIR 的调用方必须改用 `environment_id`；`project_subdir` 的语义被环境吸收，调用方需要知道路径基准变了。
- **未冻结**：`GET /v1/environments/{environment_id}` 仍是环境生成与依赖修复服务提案，需写入 `contract-phase/interfaces/` 并经双方确认；「环境不存在」用哪个错误码共享错误码表尚未定义，mock 暂以 `404` + `EXEC_4002` 承载。
- **外部评审后的加固（2026-09-24）**：`verification.recheck_command` 由可选改为**必填**——`recheck_ok` 是「补丁是否真的消除依赖问题」的唯一依据，可选时它与「fixed 非空则三项为真」的不变式互相矛盾；`repository.commit` 收紧为完整 40 位（缩写会让「同一版本」变成模糊判断）；人工基线样例的存储域明确为 `oracle` 并进入校验；mock 新增报告可用性检查（类型、所属环境、有无可修目标）与执行阶段的版本比对，并让仓库版本解析与产物记录自检挡住「产出违约却看起来正常」。见 `docs/AI_USAGE.md` 第四轮第 20 条。
- **遗留缺口**：样例里 `iterations[].build_log_artifact_id` 与 `verification_log_artifact_id` 引用的产物编号尚无对应产物记录；`runtime_capabilities` 目前只声明、未在 mock 中校验。
- **可执行证据**：`scripts/validate.py` 检查 14 逐条核对以上约定（环境可查、镜像产物归属、DRAFT 产物 `environment_id` 为 null、REPAIR 不内嵌环境、报告编号与 URI 一致、样例提交真实存在）；`scripts/mock_server.py` 提供环境查询端点，未登记的环境使任务在受理阶段被 `400` 拒绝。

## 相关文件

- `contracts/job-output-draft.schema.json`、`contracts/job-input-repair.schema.json`
- `contracts/environment.schema.json`、`contracts/repository.schema.json`
- `contracts/samples/environment.*.json`、`artifact.image-ref-*.json`
- `scripts/validate.py`（检查 06、14）、`scripts/mock_server.py`
- `docs/接口说明.md`（第 4.1 节、第 9 节、第 11 节、第 16 节）
