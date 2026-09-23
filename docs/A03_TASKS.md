# A03 E2 任务清单

本清单只覆盖 A 组负责的 BuildChecker（全量检测）与 EChecker（增量检测）。

## E2 接口契约

- ✅ 审核 B03 提供的 `FULL_CHECK` 与 `INCREMENTAL_CHECK` 输入结构。
- ✅ 接受由 DRAFT 输出 `configuration_id`，检测任务原样沿用。
- ✅ 接受 `report.commit`，用于修复任务在受理阶段核对报告版本。
- ✅ 定义依赖图文件格式：`contracts/dependency-graph.schema.json`。
- ✅ 定义全量检测输出：`contracts/job-output-full-check.schema.json`。
- ✅ 定义增量检测输出：`contracts/job-output-incremental-check.schema.json`。
- ✅ 用 ADR-008 记录 BuildChecker 的输出与依赖图交付决策。
- ✅ 用 ADR-009 记录 EChecker 的基线身份与变化表达决策。
- ✅ 提供两类检测成功响应样例。
- ✅ 为两类检测样例提供可读取的图、报告与 artifact 记录，并校验摘要。
- ✅ A03 检测样例改用仓库中可验证的真实提交 SHA。
- ✅ 将两类输出接入 `scripts/validate.py`。
- ✅ 固化 A03 对 B03 待确认事项的回复。
- ✅ 产物读取统一使用 `GET /v1/artifacts/{artifact_id}`，下载后核对 `sha256`。
- ✅ E2 联调采用同机本地镜像；跨主机部署时再切换为镜像仓库地址。
- ✅ E2 不要求 B03 额外交付可执行文件或中间目标文件，检测任务在镜像内自行构建。

## 验收命令

```bash
python -m pip install -r requirements.txt
python scripts/validate.py
```
