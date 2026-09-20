# DevOps

构建环境生成与依赖问题的接口契约、参考实现与设计记录。

## 这个仓库做什么

平台解决的是同一类问题：**构建脚本里声明的依赖，与构建过程实际用到的依赖，对不上。**

四类耗时操作共享一套任务模型与错误语义：

| 操作 | job_type | 职责 |
| --- | --- | --- |
| 生成构建环境 | `DRAFT` | 由仓库生成可成功构建的 Dockerfile |
| 全量依赖检测 | `FULL_CHECK` | 检出缺失依赖与冗余依赖 |
| 增量依赖检测 | `INCREMENTAL_CHECK` | 在连续提交间检测依赖问题变化 |
| 依赖修复 | `REPAIR` | 依据检测报告生成修复补丁 |

当前已完成 **DRAFT** 一侧的契约与参考实现。

## 目录结构

```
contracts/                        接口契约（核心）
  task.schema.json                统一任务模型
  job-create-request.schema.json  创建请求
  job-input-draft.schema.json     DRAFT 输入
  job-output-draft.schema.json    DRAFT 输出
  artifact.schema.json            产物记录
  error-codes.md                  错误码与状态语义
  samples/                        请求 / 响应 / 错误 / 产物样例

scripts/
  validate.py                     契约校验，40 项检查
  mock_server.py                  DRAFT 服务最小可跑实现（零依赖）

fixtures/draft/                   DRAFT 的固定输入样例（最小 GNU Make C 项目）
  main.c  Makefile  README.md
  docker/  Dockerfile.ok  Dockerfile.broken

docs/
  接口说明.md                      ★ 面向下游消费方的对接文档
  ADR/                            架构决策记录
  AI_USAGE.md                     设计过程与 AI 使用记录
  backlog.md                      进展与待办
```

## 快速开始

### 校验契约

```bash
python -m pip install jsonschema
python scripts/validate.py
```

预期：`全部通过：40 / 40 项检查`，退出码 0。

校验覆盖有效样例、未知 `job_type` 被拒绝、必填输入缺失被拒绝、成功与失败语义互斥、未结束的任务不携带结果、跨 schema 枚举一致性、产物摘要与文件实际内容一致、错误码与文档一致、成功任务的输出不变式。

### 跑通任务生命周期

```bash
python scripts/mock_server.py --port 8080
```

另开一个终端：

```bash
# 创建任务，立即返回 202 + QUEUED
curl -s -X POST http://127.0.0.1:8080/v1/dockerfile-jobs \
     -H 'Content-Type: application/json' \
     -d @contracts/samples/create-dockerfile-job.request.json

# 查询状态与结果
curl -s http://127.0.0.1:8080/v1/jobs/<job_id>

# 取产物
curl -s http://127.0.0.1:8080/v1/artifacts/<artifact_id>
```

### 构建样例项目

需 Docker（宿主开发机为 Windows + MSYS，无 `make`/`gcc`，构建一律在 Linux 容器中进行）。

```bash
docker build -f fixtures/draft/docker/Dockerfile.ok     -t draft-fixture-ok     fixtures/draft
docker run   --rm draft-fixture-ok      # 预期输出 hello draft

docker build -f fixtures/draft/docker/Dockerfile.broken -t draft-fixture-broken fixtures/draft
# 预期失败，日志含 make: not found
```

## 关键约定

**任务采用异步模型。** 创建接口只做受理，返回 `202` 与 `job_id`，执行在后台进行；结果通过 `GET /v1/jobs/{job_id}` 查询。理由见 `docs/ADR/ADR-001`。

**大产物以 URI 引用交接，不内嵌进响应。** 任务响应只承载元数据与小结果。理由见 `docs/ADR/ADR-002`。

**「发现问题」与「执行失败」分开表示。** 检出缺失依赖是**成功**完成任务（`status: SUCCEEDED`），基础设施故障才写 `error`。这条混淆会让下游把成功的分析当成故障重试。理由见 `docs/ADR/ADR-003` 与 `contracts/error-codes.md`。

**`output` 与 `error` 只在终态出现。** `QUEUED` / `RUNNING` 阶段只返回任务元数据，消费方因此只需面对一种输出形状。

下游对接请看 **`docs/接口说明.md`**，其中含字段用途、联调检查清单与完整走查。

## 运行环境

| 项 | 版本 |
| --- | --- |
| Python | 3.11（`validate.py` 需 `jsonschema`；`mock_server.py` 零依赖） |
| Docker | 当前契约不要求；构建样例项目时需要 |

宿主开发机为 Windows + MSYS，**无 `make`、无 `gcc`**，构建动作全部通过 Docker 在 Linux 环境中执行。
