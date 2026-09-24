# DevOps

构建环境生成与依赖问题的接口契约、参考实现与设计记录。

## 这个仓库做什么

平台解决的是同一类问题：**构建脚本里声明的依赖，与构建过程实际用到的依赖，对不上。**

四类耗时操作共享一套任务模型、错误语义与产物交接约定：

| 操作 | job_type | 端点 | 状态 |
| --- | --- | --- | --- |
| 生成构建环境 | `DRAFT` | `POST /v1/dockerfile-jobs` | 已迁移 2.0，待共同分支冻结 |
| 全量依赖检测 | `FULL_CHECK` | `POST /v1/full-check-jobs` | 已迁移 2.0，待共同分支冻结 |
| 增量依赖检测 | `INCREMENTAL_CHECK` | `POST /v1/incremental-check-jobs` | 已迁移 2.0，待共同分支冻结 |
| 依赖修复 | `REPAIR` | `POST /v1/repair-jobs` | 已迁移 2.0，待共同分支冻结 |

> 双方共同契约的权威来源是
> [`241880199/DevOps` 的 `contract-phase` 分支](https://github.com/241880199/DevOps/tree/contract-phase)。
> 本分支的四类任务 Schema 是双方按共享结构整理的可执行提案（依赖检测服务见 ADR-010，环境生成与依赖修复服务见 ADR-011），
> 逐接口确认写入 `contract-phase/interfaces/` 之后才称为冻结。

## 目录结构

```
contracts/                            接口契约（核心）
  task.schema.json                    统一任务模型（2.0，四类任务共用）
  task-legacy-v1.schema.json          1.0 历史结构，仅供追溯，已无样例引用
  job-create-request.schema.json      创建请求
  job-input-{draft,full-check,incremental-check,repair}.schema.json
  job-output-{draft,full-check,incremental-check,repair}.schema.json
  dependency-graph.schema.json         检测服务交换的依赖图
  repository.schema.json               共同仓库与版本身份
  environment.schema.json              共同环境对象；任务按 environment_id 引用
  error-report.schema.json            依赖问题报告
  artifact.schema.json                产物记录
  error-codes.md                      错误码与状态语义
  samples/                            请求 / 响应 / 错误 / 产物样例

scripts/
  validate.py                         契约校验，退出码 0 表示全部通过
  mock_server.py                      统一受理与生命周期 mock；DRAFT/REPAIR 含固定模拟输出

fixtures/
  detection/                          FULL_CHECK / INCREMENTAL_CHECK 的人工契约样例产物
  draft/                              DRAFT 的固定输入样例（最小 GNU Make C 项目）
    main.c  Makefile  README.md
    docker/  Dockerfile.ok  Dockerfile.broken  image-ref.txt
  mdfixer/                            REPAIR 的固定输入样例
    main.c  config.h  feature.h  unused.h  Makefile  README.md
    error-report.json                 人工固定报告（每条发现都指向磁盘上真实的规则行）
    reference.patch                   参考修复补丁
    docker/  Dockerfile.reference  image-ref.txt

docs/
  接口说明.md                          ★ 面向下游消费方的对接文档
  ADR/                                架构决策记录 001–011
  AI_USAGE.md                         设计过程与 AI 使用记录
  接口交换记录.md               与依赖检测服务的三轮接口交换（含待确认项）
  backlog.md                          进展与待办
  检测侧任务清单.md                        依赖检测服务的 E2 验收清单
```

## 快速开始

### 校验契约

```bash
python -m pip install jsonschema
python scripts/validate.py
```

预期：全部检查通过，退出码 0。实际检查数量会随契约和基线扩展而增加。

校验覆盖：四类任务的请求与响应、未知 `job_type` 被拒绝、各类型必填输入缺失被拒绝、成功与失败语义互斥、未结束的任务不携带结果、跨 schema 枚举与覆盖一致性、产物摘要与文件内容一致、错误码与文档一致、成功任务的输出不变式、修复只消费缺失依赖、修复失败边界（`rejected[]` vs `job.error`）、修复固定输入与磁盘文件一致、依赖检测服务检测产物的真实提交与基线祖先关系，以及环境交接（任务只按 `environment_id` 引用环境、镜像产物归属、报告编号与 URI 一致、样例提交真实存在）。

### 跑通任务生命周期

```bash
python scripts/mock_server.py --port 8080
```

另开一个终端：

```bash
curl -s -X POST http://127.0.0.1:8080/v1/dockerfile-jobs \
     -H 'Content-Type: application/json' \
     -d @contracts/samples/create-dockerfile-job.request.json

curl -s http://127.0.0.1:8080/v1/jobs/<job_id>
curl -s http://127.0.0.1:8080/v1/artifacts/<artifact_id>
curl -s http://127.0.0.1:8080/v1/environments/<environment_id>
```

四类任务均有符合专用输出 Schema 的模拟执行路径。`FULL_CHECK` 与 `INCREMENTAL_CHECK` 会动态登记可下载的图和报告，但不会运行真实检测器；空图、空报告仅用于验证契约和交接流程。

### 构建样例项目

需 Docker（宿主开发机为 Windows + MSYS，无 `make`/`gcc`，构建一律在 Linux 容器中进行）。

```bash
docker build -f fixtures/draft/docker/Dockerfile.ok     -t draft-fixture-ok     fixtures/draft
docker run   --rm draft-fixture-ok      # 预期输出 hello E3

docker build -f fixtures/draft/docker/Dockerfile.broken -t draft-fixture-broken fixtures/draft
# 预期失败，日志含 make: not found

# 修复样例的环境
docker build -f fixtures/mdfixer/docker/Dockerfile.reference -t mdfixer-fixture fixtures/mdfixer
docker run   --rm mdfixer-fixture       # 预期输出 12
```

## 关键约定

**任务采用异步模型。** 创建接口只做受理，返回 `202` 与 `job_id`，执行在后台进行；结果通过 `GET /v1/jobs/{job_id}` 查询。理由见 `docs/ADR/ADR-001`。

**大产物以 URI 引用交接，不内嵌进响应。** 任务响应只承载元数据与小结果；取用一律经 `GET /v1/artifacts/{artifact_id}`，`uri` 是给人的原始地址而不是寻址依据。理由见 `docs/ADR/ADR-002`。

**环境是独立对象，任务只按 `environment_id` 引用。** 环境由环境生成服务产出并保存，完整定义经 `GET /v1/environments/{environment_id}` 取；构建命令只在环境里表达，任务输入不重复传。环境创建后不可变，改动即新建。理由见 `docs/ADR/ADR-011`。

**「发现问题」与「执行失败」分开表示。** 检出缺失依赖是**成功**完成任务（`status: SUCCEEDED`），基础设施故障才写 `error`。这条混淆会让下游把成功的分析当成故障重试。理由见 `docs/ADR/ADR-003`。

**`output` 与 `error` 只在终态出现。** `QUEUED` / `RUNNING` 阶段只返回任务元数据，消费方因此只需面对一种输出形状。理由见 `docs/ADR/ADR-004`。

**依赖问题报告必须带位置与证据，并显式区分工具与人工来源。** 光有 `(target, dependency)` 无法复核；靠解析检测器名称区分人工答案会让统计静默失真。理由见 `docs/ADR/ADR-005`。

**修复的成功判据是「给出判断」，不是「产出补丁」。** 全部候选都被拒绝时任务仍是 `SUCCEEDED`（`fixed: []` + `rejected` 非空）——判成 `FAILED` 的话，那些拒绝理由就没有地方存放了。理由见 `docs/ADR/ADR-006`。

**报告必须属于当前源码版本。** 修复请求用 `report.commit` 把版本校验前移到受理阶段，不一致立即拒绝，不白跑一次调度。理由见 `docs/ADR/ADR-006`。

下游对接请看 **`docs/接口说明.md`**，其中含字段用途、联调检查清单与完整走查。
与依赖检测服务的接口交换记录见 **`docs/接口交换记录.md`**。

## 运行环境

| 项 | 版本 |
| --- | --- |
| Python | 3.11（`validate.py` 与 `mock_server.py` 均需 `jsonschema`） |
| Docker | 当前契约不要求；构建样例项目时需要 |

宿主开发机为 Windows + MSYS，**无 `make`、无 `gcc`**，构建动作全部通过 Docker 在 Linux 环境中执行。
