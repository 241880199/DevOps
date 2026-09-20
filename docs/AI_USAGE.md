# AI_USAGE

本文件记录设计过程中 AI 的参与情况：AI 提出了什么、人工如何判断、如何验证。
目的是让设计决策可追溯，而不是事后追认。

## 使用的工具与范围

| 项 | 内容 |
| --- | --- |
| 工具 | Claude Code（CLI） |
| 参与范围 | 契约结构设计、schema 起草、样例编写、校验脚本与模拟服务实现、文档起草 |
| 未参与 | 三处关键契约决策的最终取舍（见下）、产物校验结论的采信 |

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
- **代价**：`scripts/validate.py` 因此需要一个第三方依赖（`mock_server.py` 仍为零依赖）。
- **补充**：为约束「schema 内部自身」的一致性，追加了检查 05——跨 schema 校验 `job_type` 枚举是否一致，防止 `task.schema.json` 与 `job-create-request.schema.json` 的枚举各自演化。

## 验证方式

所有结论均由可重复执行的命令产出，未采信「看起来正确」的推断：

```bash
python scripts/validate.py     # 27 项契约检查
python scripts/mock_server.py  # 配合端到端流程验证
```

## 结论

AI 在结构设计与实现层面提供了有效加速，但在**数值真实性**与**语义完整性**两处出现了需要人工纠正的问题（第 2 条与第 4 条）。这两类问题有一个共同特征：产出**看起来是自洽的**，只有实际执行校验或核算数值才会暴露。

因此本项目的约定是：AI 产出的任何数值与约束，必须由可执行脚本验证后才计入结论。
