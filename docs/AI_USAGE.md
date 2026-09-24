# AI_USAGE

本文件记录设计过程中 AI 的参与情况：AI 提出了什么、人工如何判断、如何验证。
目的是让设计决策可追溯，而不是事后追认。

按阶段分三轮：**第一轮**是契约从零搭起（E1），**第二轮**是在已有契约上找缺口（E2，B 组），**第三轮**补齐 A03 的检测契约与设计记录。
三轮的问题类型不同，分开记录。

## 使用的工具与范围

| 项 | 内容 |
| --- | --- |
| 工具 | Claude Code（CLI，第一、二轮）；Codex（第三轮） |
| 参与范围 | 契约结构设计、schema 起草、样例编写、校验脚本与模拟服务实现、文档起草 |
| 未参与 | 关键契约决策的最终取舍（见下）、产物校验结论的采信 |

---

# 第一轮：契约搭建

## 逐条记录

### 1. 产物以 URI 引用而非内嵌进响应

- **AI 建议**：把 Dockerfile、构建日志等作为独立产物记录，任务响应只放 `artifact_id` 与 URI。
- **处理**：采纳。
- **人工补充**：AI 初版只给了 `artifact_id` / `type` / `uri` / `media_type` / `producer_job_id`。人工追加了 `sha256` 与 `source_commit` 两个字段——前者用于跨服务交接时核验内容完整，后者保证任一产物都能回溯到它产出的源码版本。仅凭 AI 初版，产物与源码版本的对应关系会丢失。
- **验证**：`scripts/validate.py` 检查 01 校验 `contracts/samples/artifact.json`；`scripts/mock_server.py` 的下载接口实际返回了 539 字节内容，证明引用可被下游解析。

### 2. 样例中的 sha256 使用了占位值

- **AI 产出**：`contracts/samples/artifact.json` 中的 `sha256` 被填为 `e3b0c442...b855`。
- **问题**：该值是**空字符串的 SHA-256**，是一个看起来合理、实际无意义的占位符。若不核对，会让样例给人「已验证」的错觉。
- **处理**：**人工核算真实摘要后替换**。取值来自 `fixtures/draft/docker/Dockerfile.ok` 的实际内容摘要（`4120822b...5846`，539 字节），`size_bytes` 同步更正。
- **理由**：样例是契约的一部分，样例中的数字必须真实可取，否则下游会照抄错误结构。
- **后续加固**：该问题后来被固化为自动检查（`validate.py` 检查 06）。此后参考 Dockerfile 只要改动一个字符，摘要不一致就会直接导致校验失败——这类「看起来合理但已过期」的数字不再依赖人工发现。

### 3. DRAFT 样例项目是否引入第三方依赖

- **AI 建议**：让 `main.c` 依赖 zlib，以便覆盖「缺少 -dev 包」这一失败模式。
- **处理**：**未采纳**。
- **理由**：样例的可信度来自判据最小、变量唯一。引入 zlib 后，构建失败的可能原因从「工具链缺失」扩散到「工具链缺失」与「开发包缺失」两类，反而不易判断对错。失败模式的覆盖交由独立的故障样例（`Dockerfile.broken`）承担，主样例保持零依赖。
- **验证**：`fixtures/draft/README.md` 明确记录了零依赖与两层判据。

### 4. schema 未约束「成功任务不得携带 error」

- **AI 产出**：`contracts/task.schema.json` 初版只约束了 `SUCCEEDED 必须含 output`、`FAILED 必须含 error`，**未禁止** `SUCCEEDED` 同时携带 `error`。
- **发现方式**：`scripts/validate.py` 检查 04 中的人工编写反例「SUCCEEDED 同时携带 error 应被拒绝」执行失败。
- **处理**：**修改 schema，而非删除该检查**。在 `allOf` 的成功分支中追加 `"not": {"required": ["error"]}`。
- **理由**：检查反映的是设计意图（成功与失败语义互斥，见 ADR-003），schema 未表达该意图属于实现缺陷。削弱检查等于把缺陷固化下来。
- **验证**：修复后 `scripts/validate.py` 27/27 项通过。

### 5. 校验脚本的实现方式

- **AI 建议**：使用 `jsonschema` 库做真实 schema 校验。
- **处理**：采纳。
- **理由**：schema 应当是契约的唯一事实来源。若手写校验逻辑，会与 schema 产生第二份真相，两者漂移后无法察觉。
- **代价**：脚本因此需要一个第三方依赖。
- **补充**：为约束「schema 内部自身」的一致性，追加了检查 05——跨 schema 校验 `job_type` 枚举是否一致，并确认每个 `job_type` 都有对应的输入契约。
- **后续修正（同一问题的复发）**：`mock_server.py` 起初对 DRAFT 输入另写了一套手写校验。这个决定与本条理由直接矛盾——同一份契约因此存在两套判断逻辑。扩展为四个端点时该矛盾被放大（需要写四套），遂改为两个脚本统一按 `contracts/*.schema.json` 校验。修正后的结果是：`mock_server.py` 也不再是零依赖。

### 6. 检测类任务契约的归属

- **AI 建议**：为实现「四个创建端点」，直接依据接口约定的描述为 `FULL_CHECK` 与 `INCREMENTAL_CHECK` 写出输入契约。
- **处理**：**采纳但加约束**——两份契约明确标注为「待检测方确认」，并写入 `docs/接口说明.md` 的待确认事项。
- **理由**：这两个任务的输入形状属于检测服务。我方单方面定稿会造成既成事实，对方只能在已落地的基础上提异议。写出来但标明待确认，对方接受或修订的成本都很低。
- **验证**：`contracts/job-input-full-check.schema.json` 与 `job-input-incremental-check.schema.json` 的 `description` 中均标明；`docs/接口说明.md` 第 10 节标题为「检测类任务契约（待确认）」。

## 验证方式

所有结论均由可重复执行的命令产出，未采信「看起来正确」的推断：

```bash
python scripts/validate.py     # 契约检查
python scripts/mock_server.py  # 配合端到端流程验证
```

---

## 第一轮结论

AI 在结构设计与实现层面提供了有效加速，但在两处出现了需要人工纠正的问题：

- **数值与约束的真实性**（第 2 条占位摘要、第 4 条 schema 缺少互斥约束）
- **设计原则的自相矛盾**（第 5 条后续修正：`mock_server.py` 违背了「schema 是唯一事实来源」而另写了一套校验）

这两类问题有一个共同特征：产出**看起来是自洽的**。占位摘要看起来像一个合法的哈希，缺失的约束不会让任何现有样例失败，重复的校验逻辑在只有一种任务类型时完全够用——只有扩展到四类、或实际核算数值，矛盾才会暴露。

因此本项目的约定是：

1. AI 产出的任何数值与约束，必须由可执行脚本验证后才计入结论。
2. 同一份契约只能有一处判断逻辑；发现第二处即视为缺陷，即使它当前工作正常。

---

# 第二轮（E2 阶段，B 组）

第一轮记录的是契约从零搭起的过程。这一轮是**在已有契约上找缺口**——交付物看起来是完整的，
问题都藏在「断言了但没验证」的地方。以下四条按发现顺序记录。

### 7. 修复阶段有错误码枚举，却没有任何错误码

- **发现方式**：核对 `task.schema.json` 时注意到 `error.stage` 的枚举是
  `ENV` / `EXEC` / `ANALYSIS` / `REPAIR`，而 `contracts/error-codes.md` 的编码规则写着
  「阶段取 `ENV` / `EXEC` / `ANALYSIS` / `REPAIR`」，紧接着的分段表却只有 `3xxx` / `4xxx` / `5xxx`。
  **`REPAIR` 阶段的四位数字段与错误码都不存在。**
- **性质**：这不是笔误。文档两处互相矛盾，而没有任何样例触及 `REPAIR` 阶段的失败，所以
  校验全绿。检查 07 只保证「样例用到的码都在文档里」，不保证「文档声明的阶段都有码」——
  **检查的方向是单向的，缺口正好落在没有样例的那一侧。**
- **处理**：补 `6xxx` 修复段与 `REPAIR_6001`（修复输入不可用）/ `REPAIR_6002`（修复过程无法进行），
  并补一个失败样例 `job.failed.repair6001.json`。新增检查 10 强制 `stage` 枚举覆盖 `REPAIR`、
  且两个码在文档中有定义。
- **验证**：`python scripts/validate.py` 检查 10。

### 8. 「REPAIR 成功任务至少修复一处」是个错误的断言

- **来源**：`validate.py` 检查 08 原有的一句不变式。
- **问题**：它把「产出补丁」当成了修复成功的特征。但「所有候选都被拒绝，并逐条给出了理由」
  同样是修复服务正常完成工作的结果。更关键的是：**若把这种情形判为 `FAILED`，`rejected[]`
  里的理由就没有地方放了**——`FAILED` 的任务没有 `output`。
- **处理**：**不是删掉这条检查，而是换成更严的一条**。新不变式分两支：
  `fixed` 非空 ⟹ `verification` 三项为真；`fixed` 为空 ⟹ `rejected` 非空且不带 `patch_artifact_id`。
  前者与原检查等价且适用范围更广，后者是新增的约束。决策记入 `docs/ADR/ADR-006`。
- **理由**：原检查反映的设计意图是对的（成功要有交付），但把「交付」窄化成了「补丁」。
  放宽到「补丁或理由」，覆盖面反而更宽——原先「`fixed: []` 且 `rejected: []`」这种
  「什么都没说明」的情形是漏网的。

### 9. 仓库存了「看起来合理」但没有实物支撑的报告

- **来源**：`contracts/samples/error-report.json` 声称 `Makefile` 第 7 行的 `main.o` 规则
  「只声明了 `main.c`」，而该行实际写的是 `main.o: main.c unused.h`；且报告没有任何
  对应的源码项目，`location` 指向一行不存在的声明。
- **性质**：与第一轮第 2 条（占位 sha256）**是同一类问题的复发**——数值与位置看起来合理，
  但无法核验。第一轮把摘要固化成了检查 06，位置这一项当时没被覆盖。
- **处理**：新建 `fixtures/mdfixer/`（真实项目 + 人工固定报告 + 参考补丁 + 参考镜像），
  把 `location.line` 与 `detail` 改成与磁盘文件一致；新增检查 11，逐条核验
  每条发现的 `location.file` 存在、`location.line` 指向的确实是该 `target` 的规则行。
- **验证**：`python scripts/validate.py` 检查 11。
- **补充**：这次加固抓到了一个真实缺陷——**参考补丁本身写错了**。补丁把
  `main.o: main.c unused.h` 整行替换为 `main.o: main.c config.h feature.h`，
  顺带删掉了 `unused.h` 这条冗余声明。而修复只针对缺失依赖，冗余声明的去留不由修复决定。
  检查 11 里「应用补丁后冗余声明必须原样保留」这一条直接把它拦下并改正。
  如果只是把补丁写进文档、不写检查，这个越界改动会一路带到 E3。

### 10. 样例里有一个没人产出的字段

- **来源**：`contracts/job-input-full-check.schema.json` 要求 `environment.configuration_id`，
  但检查四类服务的输出，**没有任何一个服务产出它**。检测方只能自己发明标识。
- **性质**：这与第 6 条（检测类契约的归属）同源。当时把「`FULL_CHECK` 的形状我方拟出」
  标为待确认，但没注意到**这个形状依赖一个不存在的上游字段**——待确认事项里挂了，
  却没有对应的提案。
- **处理**：**采纳但限定为可选**。在 `job-output-draft.schema.json` 中新增可选
  `configuration_id`，由环境产出方回报，检测方直接沿用。理由与第 6 条一致：可选字段是
  兼容变更，对方接受或修订的成本都很低；若设为必填则破坏兼容，必须递增 `schema_version`，
  在 E2 阶段代价过大。决策记入 `docs/ADR/ADR-007`，状态标为**待 A 组确认**。
- **验证**：`contracts/samples/job.succeeded.json` 已回报该字段；`mock_server.py` 的
  `output_for_draft` 同步填充。

### 本轮的两条结论

**一、「断言了但没验证」的缺口，只会出现在没有样例覆盖的那一侧。** 本轮四个问题
（缺 `REPAIR` 错误码、错误的成功不变式、无实物的报告、没人产出的字段）有一个共同点：
文档里都写了，代码也都跑得通，但**没有任何一条检查会把它们照出来**。检查 07 只做
「样例 → 文档」的单向核对，缺口正好在反向。补检查时优先补的是**方向相反**的那一条。

**二、把实物放进仓库，比把描述写进文档更能发现问题。** 第 9 条那个越界补丁，
是被「把补丁真的写进仓库、再让检查去应用它」逼出来的；只写进文档时，它是一段
看起来完全合理的 diff。这与第一轮第 2 条的结论是同一条，只是这次作用在文件内容上，
而不只是数值上。

## 验证方式（第二轮）

```bash
python scripts/validate.py     # 110 项契约检查，退出码 0
python scripts/mock_server.py  # REPAIR 报告版本检查与 DRAFT 输出实测
```

新增检查：10（修复失败边界与错误码）、11（修复固定输入与磁盘文件一致）；
扩展检查：06（摘要核验覆盖补丁产物）、08（REPAIR 两支不变式）。

# 第三轮（E2 阶段，A 组）

本轮使用 Codex，范围是补齐 A03 负责的全量/增量检测输出契约。
### 11. 检测任务只有输入契约，没有专用输出契约

- **来源**：仓库已有 `job-input-full-check.schema.json` 与 `job-input-incremental-check.schema.json`，但输出侧只有 DRAFT 和 REPAIR 的专用 schema；`task.schema.json` 只约束 `output` 是一个对象。
- **问题**：现有约束无法证明 BuildChecker 一定交付实际图、声明图和错误报告，也无法证明 EChecker 返回了下一次增量检测可复用的图。即使检测任务返回空对象，统一任务模型仍可能接受。
- **AI 建议**：为两类检测分别建立专用输出 schema，并增加一份统一的依赖图 schema。
- **处理**：采纳。新增 `job-output-full-check.schema.json`、`job-output-incremental-check.schema.json` 和 `dependency-graph.schema.json`，并各补一份成功响应样例。
- **人工补充**：全量输出必须同时引用实际图、声明图和错误报告；增量输出必须同时保存 `base_commit`、当前提交和 `configuration_id`，避免图与源码版本脱节。
- **验证**：`scripts/validate.py` 检查 01、05、12 校验两类输出样例、四类输出契约覆盖以及版本字段一致性。

### 12. 增量结果只给当前报告，无法说明错误怎样变化

- **AI 建议**：EChecker 输出当前完整报告，并用 `changes.added` 与 `changes.resolved` 表达相对历史基线的变化。
- **处理**：采纳。
- **理由**：MDFixer 需要的是当前仍有效的 MD，不能只收到差异；完整报告和变化列表承担不同用途，不能互相替代。
- **约束补充**：变化项沿用错误报告 finding 的核心字段，包括 `commit`、`provenance`、`location` 与 `evidence`，不允许退化成只有 `(target, dependency)` 的记录。
- **验证**：`job.incremental-check.succeeded.json` 给出一条新增的 `main.o -> feature.h`；检查 12 同时核对 `base_commit` 与 `configuration_id`。



### 13. A 组关键输出只有 Schema，没有对应 ADR

- **来源**：A03 新增了 `dependency-graph.schema.json`、`job-output-full-check.schema.json` 与 `job-output-incremental-check.schema.json`，但 `docs/ADR/` 中原有记录只覆盖公共任务模型、产物交接、错误语义和 B 组的 DRAFT / REPAIR 决策。
- **问题**：Schema 表达了最终字段，却没有记录为什么 BuildChecker 要分别交付实际图、声明图和错误报告，也没有记录为什么 EChecker 要同时返回当前完整报告与新增/消除变化。评审者只能看到结果，无法追溯备选方案和代价。
- **AI 建议**：分别新增 BuildChecker 输出组织和 EChecker 基线语义的 ADR，避免把两项独立决策压缩成一份过大的记录。
- **处理**：采纳。新增 `docs/ADR/ADR-008-BuildChecker输出与依赖图交付.md` 与 `docs/ADR/ADR-009-EChecker基线身份与变化表达.md`。
- **人工补充**：两份 ADR 只记录 A03 的检测服务决策，不修改 ADR-001 至 ADR-007，也不替 B03 确认 DRAFT、REPAIR 或部署方式。
- **验证**：`docs/A03_TASKS.md` 与 `docs/backlog.md` 已加入两份 ADR 的索引，`docs/接口说明.md` 的 FULL_CHECK / INCREMENTAL_CHECK 输出段落已链接对应决策记录。

### 14. ADR 确认状态与配对记录不一致

- **来源**：ADR-007 仍标为“待 A 组确认”，但 `docs/A03_TASKS.md` 和 `docs/配对组接口交换记录.md` 已写明 A03 接受由 DRAFT 回报 `configuration_id`；ADR-002 已采纳 URI 引用方案，但实际读取方式仍未选择；ADR-006 中 `REPAIR_6001` 的两种载体也仍在待确认列表。
- **问题**：同一决定在不同文件中同时呈现“已接受”和“待确认”，容易让评审者误判双方是否已经达成一致。A03 也不能通过直接改写 B03 的既有 ADR 来代替配对确认。
- **AI 建议**：保留既有 ADR 原文，由 A03 在自己的任务清单和配对回复中记录接受范围；需要双方决定的 URI 读取方式、镜像交付位置和错误载体继续保留为未决事项。
- **处理**：采纳“不修改已有 ADR”的边界。本轮只新增 A03 的 ADR-008、ADR-009，并保留现有联合待确认项。
- **人工补充**：ADR-007 的最终状态应由原决策维护方在收到 A03 回复后更新；A03 的接受证据继续以 `docs/A03_TASKS.md` 和 `docs/配对组接口交换记录.md` 为准。
- **验证**：确认 ADR-001 至 ADR-007 没有内容变更；新增文件编号从 ADR-008 开始，未覆盖已有记录。



### 15. 检测类 mock 成功输出违反自身 Schema，且缺少分析失败样例

- **来源**：`scripts/mock_server.py` 对 `FULL_CHECK` / `INCREMENTAL_CHECK` 使用通用
  `output_for_other`，只返回 `note`，随后却把任务标为 `SUCCEEDED`；该对象不符合两类
  A03 专用输出 Schema。错误码表已定义 `ANALYSIS_5001`，但没有对应失败样例。
- **问题**：HTTP 运行结果与静态成功样例相互矛盾；校验脚本只能证明样例正确，不能防止
  mock 在运行时生成非法成功结果。分析器崩溃也缺少可供配对组复核的任务形状。
- **AI 建议**：为两类检测分别生成动态空图和空报告，登记为当前 job 的可下载 artifact；
  在写入 `SUCCEEDED` 前用专用输出 Schema 自检，并补充 `ANALYSIS_5001` 失败样例和
  可复现的 mock 注入路径。
- **处理**：采纳。mock 现在为 FULL_CHECK 生成实际图、声明图和错误报告，为
  INCREMENTAL_CHECK 生成当前实际图、错误报告及空变化集；URL 以 `fail-analysis` 结尾时
  返回 `FAILED / ANALYSIS_5001`。空图和空报告明确是契约模拟，不冒充真实检测结果。
- **人工补充**：修改仅覆盖 A03 检测任务；DRAFT / REPAIR 的输出逻辑和 B03 样例未改动。
- **验证**：`scripts/validate.py` 校验新失败样例及错误语义；HTTP 实测两类成功输出均通过
  专用 Schema、所有输出 artifact 均可下载，失败注入不携带 `output`。

### 16. 本地 A03 契约与双方公共结构发生破坏性冲突

- **来源**：共同仓库 `241880199/DevOps` 的 `contract-phase` 分支提交
  `431a7438a2487ef505b528e7bd781b2c2563862b`。
- **问题**：共同结构用 `environment_id` 取代 `configuration_id`，Job 不再内嵌环境定义，
  Repository 要求 `canonical_url` 和完整 SHA；同时产出方矩阵不允许 EChecker 产出
  `ACTUAL_GRAPH`。本地旧 Schema 虽能通过自身测试，却不符合双方共同口径。
- **AI 建议**：保留旧 ADR 作为历史，新增同步 ADR；只修改 A03 专有接口和公共结构，
  不替 B03 决定 DRAFT / REPAIR 的第二阶段字段；将可评审提案单独整理，提交到共同分支后
  再称为冻结契约。
- **处理**：采纳。新增 ADR-010 与 `docs/A03_CONTRACT_PHASE_PROPOSAL.md`；A03 输入改为
  Repository + `environment_id`，增量基线改用 Artifact ID，EChecker 输出移除实际图；
  Job 契约版本递增为 `2.0`。
- **人工补充**：个人协作记录按共同分支规则写入 `contract-phase/records/`，本实现分支只
  保留迁移说明和 Git 历史。DRAFT / REPAIR 旧专有字段明确标为待 B03 迁移。
- **验证**：自动校验新增共同契约断言，并实测 FULL_CHECK、INCREMENTAL_CHECK 和
  `ANALYSIS_5001` 路径。

## 验证方式（第三轮）

```bash
python scripts/validate.py  # 151 项契约检查，退出码 0
```

已完成 JSON/Python 语法检查和 151/151 项契约校验。
