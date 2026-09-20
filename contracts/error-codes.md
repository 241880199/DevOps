# 错误码与状态语义

本文件是整个平台各服务共同遵守的错误约定。核心是**划清一条线**：

> **「发现问题」与「工具没跑完」是两件事，必须分开表示。**

这条线是跨服务交接时最容易出错的地方，也是本文件存在的主要理由。

---

## 1. 两类结果，两处存放

| | 系统执行错误 | 正常分析发现 |
| --- | --- | --- |
| **含义** | 任务本身没跑完：环境起不来、超时、分析器崩溃 | 任务正常跑完了，并且**发现了问题** |
| **存放位置** | `job.error` | `output` / 报告产物（如 `ERROR_REPORT` 的 findings） |
| **任务终态** | `FAILED` / `TIMED_OUT` / `CANCELLED` | **仍是 `SUCCEEDED`** |
| **举例** | 镜像构建失败、任务超时 | 检出缺失依赖 MD、冗余依赖 RD |

### 最重要的一条

**检出问题不等于工具执行失败。**

检测服务报告一个缺失依赖，说明它**正常完成了工作**，任务终态是 `SUCCEEDED`。若把「发现 MD」写成 `FAILED`，下游调度会把一次成功分析当成基础设施故障，可能触发无意义的重试，甚至中断整条流水线。

对 DRAFT 而言同理：**生成不出 Dockerfile** 是系统执行错误（`ENV_3002`）；而**某轮候选构建失败、但已定位到原因并继续迭代**属于过程状态，写进 `output.iterations[].outcome`，不是 `job.error`。

对修复而言同理：**候选被评估后判定不可接受**属于过程状态，写进 `output.rejected[]`，不是 `job.error`。**即使所有候选都被拒绝**，任务终态仍是 `SUCCEEDED`——「这批候选都不可接受」是修复服务正常工作的结论，把它记成 `FAILED` 会让调度器重跑一次昂贵的修复。区分见第 6 节。

---

## 2. 已定义错误码

| 错误码 | 阶段 | 含义 | 典型 detail |
| --- | --- | --- | --- |
| `ENV_3002` | `ENV` | 镜像构建失败 | `docker build` 非零退出，附最后若干行日志 |
| `EXEC_4002` | `EXEC` | 任务超时 | 超出 `limits.timeout_seconds` 或全局墙钟上限 |
| `ANALYSIS_5001` | `ANALYSIS` | 分析器失败 | 分析进程异常退出，**非**「检出问题」 |
| `REPAIR_6001` | `REPAIR` | 修复输入不可用 | 报告产物取不到、不符合 `error-report` 契约，或报告所依据的版本与请求的 `repository.commit` 不一致 |
| `REPAIR_6002` | `REPAIR` | 修复过程无法进行 | 环境或工具故障导致补丁生成/应用本身跑不动（镜像内无 `git`/`patch`、工作副本不可写、复检工具无法启动），**非**「候选被判定为不可接受」 |

### 编码规则

`<阶段>_<四位数字>`，阶段取 `ENV` / `EXEC` / `ANALYSIS` / `REPAIR`。

四位数字按类分段，便于新增时避免冲突：

| 段 | 范围 | 用途 |
| --- | --- | --- |
| 3xxx | 环境 | 镜像、仓库拉取、工具链 |
| 4xxx | 执行 | 超时、取消、资源不足 |
| 5xxx | 分析 | 解析器、分析器内部故障 |
| 6xxx | 修复 | 修复输入、补丁生成与应用 |

> 新增错误码时必须同步更新本文件与 `task.schema.json` 的 `error.code` 约束。

### 样例

每个错误码都应有可执行的样例支撑，便于消费方对齐：

| 错误码 | 样例 |
| --- | --- |
| `ENV_3002` | `contracts/samples/job.failed.env3002.json` |
| `EXEC_4002` | `contracts/samples/job.timed_out.exec4002.json` |
| `ANALYSIS_5001` | 暂无（本服务不产生该码） |
| `REPAIR_6001` | `contracts/samples/job.failed.repair6001.json` |
| `REPAIR_6002` | 暂无（需环境缺少补丁工具才能构造，当前 mock 不覆盖） |

`scripts/validate.py` 检查 07 会扫描全部样例，确认其中出现的错误码都已在本文件中归档——防止样例与文档各自演化。

---

## 3. 各错误码的生产者

| 错误码 | 环境生成服务 | 依赖检测服务 | 依赖修复服务 |
| --- | --- | --- | --- |
| `ENV_3002` | ✅ 主要生产者：Dockerfile 无法构建出镜像 | ✅ 环境起不来 | ✅ 修复环境起不来（**非**「补丁验证未通过」） |
| `EXEC_4002` | ✅ 迭代轮数/时间超限 | ✅ | ✅ 修复轮数/时间超限 |
| `ANALYSIS_5001` | ⬜ 不产生 | ✅ 主要生产者 | ⬜ 不产生 |
| `REPAIR_6001` | ⬜ 不产生 | ⬜ 不产生 | ✅ 主要生产者 |
| `REPAIR_6002` | ⬜ 不产生 | ⬜ 不产生 | ✅ 主要生产者 |

---

## 4. 状态机

```
QUEUED ──> RUNNING ──┬──> SUCCEEDED     结果可用，output 必填
   │                 ├──> FAILED        error 必填
   │                 ├──> TIMED_OUT     error 必填
   └─────────────────┴──> CANCELLED     error 必填
```

- `QUEUED`：已受理，尚未开始。`POST` 立即返回此状态。
- `RUNNING`：后台执行中。
- 终态一经到达**不再变更**。
- 状态的取值属于契约的一部分：**删除、改名或改变状态枚举语义都是破坏兼容的变更**，消费者必须同步升级。

---

## 5. 自检

接入方在联调前应确认对以下三问答案一致：

1. 检测服务报告了一条 MD，这个 job 的 `status` 是什么？ → **`SUCCEEDED`**
2. 分析进程段错误崩溃了，这个 job 的 `status` 是什么？ → **`FAILED`**，`error.code = ANALYSIS_5001`
3. 修复服务把所有候选补丁都拒了，这个 job 的 `status` 是什么？ → **`SUCCEEDED`**，`output.fixed` 为空、`output.rejected` 非空

这三问的答案不一致，说明双方对 `error` 字段的理解有分歧，需要先对齐再联调。

---

## 6. 修复的成败边界（第 3 问展开）

修复任务的**成功判据**是「给出一个对每个候选的去留判断」，而不是「产出补丁」。据此划界：

| 情形 | 存放位置 | 任务终态 | 错误码 |
| --- | --- | --- | --- |
| 部分候选被采纳，部分被拒 | `output.fixed` + `output.rejected` | `SUCCEEDED` | 无 |
| **全部候选都被拒绝** | `output.fixed = []` + `output.rejected` 非空 | **`SUCCEEDED`** | 无 |
| 报告取不到 / 不符合契约 / 版本与请求不符 | `job.error` | `FAILED` | `REPAIR_6001` |
| 补丁生成或应用本身跑不动（缺工具、副本不可写） | `job.error` | `FAILED` | `REPAIR_6002` |

判据是**「判断能不能做」与「做完判断」**：

- `rejected[]` 是**做完判断**的结果——候选被正常评估过，只是结论为「不可接受」。这是修复服务的产出，必须逐条留痕（`rejected[].reason` 必填）。
- `REPAIR_6001` / `REPAIR_6002` 是**判断没做成**——输入不可信，或工具跑不起来。此时连一条可信的候选结论都没有。

配套不变式（由 `scripts/validate.py` 检查 08 与检查 10 校验）：

- `output.fixed` 非空 ⟹ `verification.build_ok` / `test_ok` / `recheck_ok` 三项均为 `true`；
- `output.fixed` 为空 ⟹ `output.rejected` 非空，且无 `patch_artifact_id`。

`error` 中只写**摘要**（被拒候选数、最主要的原因）；逐候选的完整理由始终在 `output.rejected[]`。因此「全部候选被拒」这一情形**不能**改用 `FAILED` 表达——`FAILED` 的任务没有 `output`，那些理由就没地方放了。

样例对照：`job.repair.succeeded.json`（部分被拒）、`job.repair.no_fix.json`（全部被拒）、`job.failed.repair6001.json`（输入不可用）。
