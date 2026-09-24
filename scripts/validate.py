#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""接口契约校验。

把接口约定的最小检查固化成可重复执行的命令：

  01  四类任务的请求与响应样例全部通过
  02  未知 job_type 被拒绝；客户端不得自定 job_id
  03  各任务类型的必填输入缺失被拒绝
  04  系统错误与正常结果不得混淆（检出依赖问题 != 执行失败）
  05  跨 schema 的枚举与覆盖一致性，防止契约漂移
  06  样例中记录的产物摘要与实际文件一致
  07  样例用到的错误码均已在 error-codes.md 中归档
  08  成功任务的输出不变式
  09  修复任务只消费缺失依赖报告
  10  修复的失败边界：rejected[] 与 job.error 各司其职
  11  修复的固定输入与磁盘上的真实文件对得上
  12  检测服务输出、可读取产物、真实提交及基线祖先关系一致
  13  检测服务契约与 contract-phase 公共结构保持一致
  14  环境交接：环境生成服务产出环境，下游任务只按 environment_id 引用

用法（在仓库根目录执行）：
    python scripts/validate.py

退出码 0 表示全部通过；非 0 表示有检查失败，失败项会逐条打印。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "contracts"
SAMPLES = CONTRACTS / "samples"
DETECTION_FIXTURES = ROOT / "fixtures" / "detection"

DETECTION_ARTIFACTS = (
    ("artifact.actual-graph-001.json", DETECTION_FIXTURES / "full-check" / "actual-graph.json", "dependency-graph"),
    ("artifact.declared-graph-001.json", DETECTION_FIXTURES / "full-check" / "declared-graph.json", "dependency-graph"),
    ("artifact.error-report-001.json", DETECTION_FIXTURES / "full-check" / "error-report.json", "error-report"),
    ("artifact.error-report-002.json", DETECTION_FIXTURES / "incremental-check" / "error-report.json", "error-report"),
)

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:  # pragma: no cover
    sys.exit(
        "缺少依赖 jsonschema。请先安装：\n"
        "    python -m pip install jsonschema"
    )

FORMAT_CHECKER = FormatChecker()


@FORMAT_CHECKER.checks("date-time", raises=(TypeError, ValueError))
def is_real_datetime(value) -> bool:
    """拒绝仅形状像时间、但日历日期不存在或缺少时区的字符串。"""
    if not isinstance(value, str):
        return True
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.tzinfo is not None

# job_type -> 专有输入 schema。与 mock_server.py 保持一致；
# 检查 05 会确认四类任务都有对应的输入契约，防止新增类型时漏配。
INPUT_SCHEMA = {
    "DRAFT": "job-input-draft",
    "FULL_CHECK": "job-input-full-check",
    "INCREMENTAL_CHECK": "job-input-incremental-check",
    "REPAIR": "job-input-repair",
}

# job_type -> 专有输出 schema（有定义的类型）
OUTPUT_SCHEMA = {
    "DRAFT": "job-output-draft",
    "FULL_CHECK": "job-output-full-check",
    "INCREMENTAL_CHECK": "job-output-incremental-check",
    "REPAIR": "job-output-repair",
}


# ---------------------------------------------------------------- 基础设施

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_schemas() -> dict:
    suffix = ".schema.json"
    return {
        p.name[: -len(suffix)]: load_json(p)
        for p in sorted(CONTRACTS.glob(f"*{suffix}"))
    }


class Report:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[tuple[str, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        if ok:
            self.passed += 1
            print(f"  [PASS] {name}")
        else:
            self.failed.append((name, detail))
            print(f"  [FAIL] {name}")
            for line in detail.splitlines():
                print(f"         {line}")

    def summary(self) -> int:
        total = self.passed + len(self.failed)
        print()
        print("=" * 68)
        if self.failed:
            print(f"失败：{len(self.failed)} / {total} 项检查未通过")
            for name, _ in self.failed:
                print(f"  - {name}")
            print("=" * 68)
            return 1
        print(f"全部通过：{self.passed} / {total} 项检查")
        print("=" * 68)
        return 0


def errors_of(schema: dict, instance) -> list[str]:
    if not schema:
        return ["契约缺失：没有可用的 schema"]
    validator = Draft202012Validator(schema, format_checker=FORMAT_CHECKER)
    out = []
    for err in sorted(validator.iter_errors(instance), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "<root>"
        out.append(f"{loc}: {err.message}")
    return out


def sample(name: str):
    path = SAMPLES / name
    if not path.exists():
        raise SystemExit(f"样例缺失：{path}")
    return load_json(path)


def create_samples() -> list[Path]:
    return sorted(SAMPLES.glob("create-*.request.json"))


def sample_environments() -> dict[str, dict]:
    """样例中的环境记录，按 environment_id 索引。"""
    return {
        record["environment_id"]: record
        for record in (load_json(p) for p in sorted(SAMPLES.glob("environment.*.json")))
    }


# ------------------------------------------------------------------ 检查项

def check_01_all_types_pass(schemas: dict, rep: Report) -> None:
    """四类任务的请求与响应样例都必须通过各自契约。"""
    print("\n检查 01：四类任务的请求与响应样例通过")

    seen_types = set()
    for path in create_samples():
        req = load_json(path)
        errs = errors_of(schemas["job-create-request"], req)
        rep.check(f"{path.name} 符合创建请求契约", not errs, "\n".join(errs))
        if errs:
            continue

        job_type = req["job_type"]
        seen_types.add(job_type)
        errs = errors_of(schemas[INPUT_SCHEMA[job_type]], req["input"])
        rep.check(f"{path.name} 的 {job_type} 输入符合契约", not errs, "\n".join(errs))

    expected = set(INPUT_SCHEMA)
    rep.check(
        f"四类任务均提供创建请求样例（已覆盖 {len(seen_types)}/4）",
        seen_types == expected,
        f"缺少：{sorted(expected - seen_types)}",
    )

    # 四类任务统一到 Job 2.0：DRAFT / REPAIR 与两类检测走同一份契约，
    # 不再存在并行的 legacy 版本——迁移完成后仍留在 1.0 的样例即为未完成。
    for name in ("job.succeeded.json", "job.full-check.succeeded.json",
                 "job.incremental-check.succeeded.json",
                 "job.repair.succeeded.json", "job.repair.no_fix.json"):
        job = sample(name)
        errs = errors_of(schemas["task"], job)
        rep.check(f"{name} 符合统一任务模型 2.0", not errs, "\n".join(errs))
        out_schema = OUTPUT_SCHEMA.get(job.get("job_type"))
        if out_schema:
            errs = errors_of(schemas[out_schema], job.get("output", {}))
            rep.check(f"{name} 的 {job['job_type']} 输出符合契约", not errs, "\n".join(errs))

    for name in ("job.running.json", "job.failed.env3002.json",
                 "job.failed.analysis5001.json",
                 "job.timed_out.exec4002.json", "job.failed.repair6001.json"):
        job = sample(name)
        errs = errors_of(schemas["task"], job)
        rep.check(f"{name} 符合统一任务模型 2.0", not errs, "\n".join(errs))

    artifact_names = ["artifact.json", "artifact.patch.json",
                      "artifact.image-ref-001.json", "artifact.image-ref-002.json",
                      "artifact.error-report-003.json"] + [item[0] for item in DETECTION_ARTIFACTS]
    for name in artifact_names:
        errs = errors_of(schemas["artifact"], sample(name))
        rep.check(f"{name} 符合 artifact.schema.json", not errs, "\n".join(errs))

    errs = errors_of(schemas["error-report"], sample("error-report.json"))
    rep.check("依赖问题报告符合 error-report.schema.json", not errs, "\n".join(errs))

    resp = sample("create-job.response.202.json")
    rep.check(
        "202 响应含 job_id / status=QUEUED",
        resp.get("status") == "QUEUED" and bool(resp.get("job_id")),
        f"实际：status={resp.get('status')!r} job_id={resp.get('job_id')!r}",
    )


def check_02_unknown_job_type_rejected(schemas: dict, rep: Report) -> None:
    print("\n检查 02：未知 job_type 必须被拒绝")

    base = load_json(create_samples()[0])
    for bad in ("ABC", "draft", ""):
        req = copy.deepcopy(base)
        req["job_type"] = bad
        rep.check(
            f"job_type={bad!r} 被拒绝",
            bool(errors_of(schemas["job-create-request"], req)),
            "未被拒绝：schema 接受了非法 job_type",
        )

    req = copy.deepcopy(base)
    req["job_id"] = "job-client-supplied"
    rep.check(
        "请求携带 job_id 被拒绝",
        bool(errors_of(schemas["job-create-request"], req)),
        "未被拒绝：客户端不应决定 job_id",
    )


def check_03_missing_required_rejected(schemas: dict, rep: Report) -> None:
    print("\n检查 03：各任务类型的必填输入缺失必须被拒绝")

    cases = [
        ("create-dockerfile-job.request.json", "DRAFT",
         [("删除 build.command", lambda i: i["build"].pop("command")),
          ("删除 build.verify_command", lambda i: i["build"].pop("verify_command")),
          ("删除 build 整块", lambda i: i.pop("build")),
          ("删除 limits（超时与轮数无约束）", lambda i: i.pop("limits")),
          ("删除 limits.timeout_seconds", lambda i: i["limits"].pop("timeout_seconds")),
          ("删除 repository", lambda i: i.pop("repository")),
          ("context_documents 超过 2 个",
           lambda i: i.__setitem__("context_documents", ["a.md", "b.md", "c.md"]))]),

        ("create-repair-job.request.json", "REPAIR",
         [("删除 report（修复无目标）", lambda i: i.pop("report")),
          ("删除 report.finding_type", lambda i: i["report"].pop("finding_type")),
          ("删除 report.artifact_id（报告无可取回的编号）",
           lambda i: i["report"].pop("artifact_id")),
          ("删除 environment_id（无从复构建与复检）", lambda i: i.pop("environment_id")),
          ("删除 verification（无从判断修改是否生效）", lambda i: i.pop("verification")),
          ("删除 verification.verify_command",
           lambda i: i["verification"].pop("verify_command")),
          ("删除 verification.recheck_command（复检是 recheck_ok 的唯一依据）",
           lambda i: i["verification"].pop("recheck_command")),
          ("repository.commit 用缩写（版本比对必须逐字符相等）",
           lambda i: i["repository"].__setitem__("commit", "31cd2ad")),
          ("删除 repository", lambda i: i.pop("repository"))]),

        ("create-full-check-job.request.json", "FULL_CHECK",
         [("删除 environment_id", lambda i: i.pop("environment_id")),
          ("删除 repository.canonical_url", lambda i: i["repository"].pop("canonical_url")),
          ("删除 repository.commit", lambda i: i["repository"].pop("commit"))]),

        ("create-incremental-check-job.request.json", "INCREMENTAL_CHECK",
         [("删除 baseline（无法判定可比性）", lambda i: i.pop("baseline")),
          ("删除 baseline.actual_graph_artifact_id",
           lambda i: i["baseline"].pop("actual_graph_artifact_id")),
          ("删除 baseline.commit", lambda i: i["baseline"].pop("commit")),
          ("删除 baseline.environment_id",
           lambda i: i["baseline"].pop("environment_id")),
          ("删除 environment_id", lambda i: i.pop("environment_id")),
          ("删除 base_commit", lambda i: i.pop("base_commit"))]),
    ]

    for name, job_type, mutators in cases:
        payload = load_json(SAMPLES / name)
        for label, mutate in mutators:
            req = copy.deepcopy(payload)
            mutate(req["input"])
            errs = errors_of(schemas[INPUT_SCHEMA[job_type]], req["input"])
            rep.check(f"[{job_type}] {label} 被拒绝", bool(errs),
                      "未被拒绝：必填字段缺失却通过了校验")


def check_04_error_semantics(schemas: dict, rep: Report) -> None:
    print("\n检查 04：系统错误与正常结果不得混淆")

    job = copy.deepcopy(sample("job.full-check.succeeded.json"))
    job.pop("output")
    rep.check("SUCCEEDED 缺少 output 被拒绝",
              bool(errors_of(schemas["task"], job)),
              "未被拒绝：成功任务必须交付结果")

    job = copy.deepcopy(sample("job.failed.analysis5001.json"))
    job.pop("error")
    rep.check("FAILED 缺少 error 被拒绝",
              bool(errors_of(schemas["task"], job)),
              "未被拒绝：失败任务必须说明原因")

    job = copy.deepcopy(sample("job.failed.analysis5001.json"))
    job["error"]["code"] = "NOT_A_CODE"
    rep.check("未在共同契约定义的错误码被拒绝",
              bool(errors_of(schemas["task"], job)),
              "未被拒绝：error.code 必须先在 contract-phase 中共同定义")

    # 关键语义：检出问题 != 执行失败。
    # 一次成功的检测任务，其 output 中允许承载发现项，任务终态仍是 SUCCEEDED。
    # 用检测类任务构造该场景——DRAFT 与 REPAIR 的专有输出契约中本就没有发现项。
    findings_job = {
        "schema_version": "2.0",
        "job_id": "job-full01",
        "job_type": "FULL_CHECK",
        "status": "SUCCEEDED",
        "execution": {
            "created_at": "2026-09-23T09:00:00.000Z",
            "started_at": "2026-09-23T09:00:01.000Z",
            "finished_at": "2026-09-23T09:00:08.000Z",
            "attempt": 1,
        },
        "input": sample("create-full-check-job.request.json")["input"],
        "output": {
            "report_artifact_id": "md-report-001",
            "findings": [
                {"type": "MISSING", "target": "main.o", "dependency": "config.h"}
            ],
        },
    }
    rep.check("SUCCEEDED 携带发现项仍然合法（检出 MD != 执行失败）",
              not errors_of(schemas["task"], findings_job),
              "被误拒：发现项不应使任务变为失败")

    job = copy.deepcopy(sample("job.full-check.succeeded.json"))
    job["error"] = {"code": "ENV_3002", "message": "不应出现"}
    rep.check("SUCCEEDED 同时携带 error 应被拒绝",
              bool(errors_of(schemas["task"], job)),
              "未被拒绝：成功与失败语义必须互斥")

    for status in ("FAILED", "TIMED_OUT", "CANCELLED"):
        job = copy.deepcopy(sample("job.failed.analysis5001.json"))
        job["status"] = status
        job["output"] = copy.deepcopy(sample("job.full-check.succeeded.json")["output"])
        rep.check(f"{status} 同时携带 output 应被拒绝",
                  bool(errors_of(schemas["task"], job)),
                  "未被拒绝：失败原因与成功产出必须互斥")

    # output 与 error 都是终态的产物
    for status in ("QUEUED", "RUNNING"):
        job = copy.deepcopy(sample("job.full-check.succeeded.json"))
        job.pop("output")
        job["status"] = status
        job["execution"]["finished_at"] = None
        if status == "QUEUED":
            job["execution"]["started_at"] = None
            job["execution"]["attempt"] = 0

        with_error = copy.deepcopy(job)
        with_error["error"] = {"code": "ENV_3002", "message": "未结束不应有 error"}
        rep.check(f"{status} 携带 error 应被拒绝",
                  bool(errors_of(schemas["task"], with_error)),
                  "未被拒绝：error 只应出现在终态")

        with_output = copy.deepcopy(job)
        with_output["output"] = copy.deepcopy(sample("job.full-check.succeeded.json")["output"])
        rep.check(f"{status} 携带 output 应被拒绝",
                  bool(errors_of(schemas["task"], with_output)),
                  "未被拒绝：output 只应出现在终态")


def check_05_consistency(schemas: dict, rep: Report) -> None:
    print("\n检查 05：跨 schema 一致性（防契约漂移）")

    task_types = set(schemas["task"]["properties"]["job_type"]["enum"])
    req_types = set(schemas["job-create-request"]["properties"]["job_type"]["enum"])
    rep.check("job_type 枚举在 task 与 create-request 中一致",
              task_types == req_types,
              f"task={sorted(task_types)}\ncreate-request={sorted(req_types)}")

    missing_in = task_types - set(INPUT_SCHEMA)
    extra_in = set(INPUT_SCHEMA) - task_types
    rep.check("每个 job_type 都有对应的输入契约",
              not missing_in and not extra_in,
              f"缺契约：{sorted(missing_in)}\n多余映射：{sorted(extra_in)}")

    for job_type, schema_name in INPUT_SCHEMA.items():
        rep.check(f"{job_type} 的输入契约 {schema_name}.schema.json 已加载",
                  schema_name in schemas,
                  f"未找到 {schema_name}.schema.json")

    missing_out = task_types - set(OUTPUT_SCHEMA)
    extra_out = set(OUTPUT_SCHEMA) - task_types
    rep.check("每个 job_type 都有对应的输出契约",
              not missing_out and not extra_out,
              f"缺契约：{sorted(missing_out)}\n多余映射：{sorted(extra_out)}")

    for job_type, schema_name in OUTPUT_SCHEMA.items():
        rep.check(f"{job_type} 的输出契约 {schema_name}.schema.json 已加载",
                  schema_name in schemas,
                  f"未找到 {schema_name}.schema.json")

    statuses = set(schemas["task"]["properties"]["status"]["enum"])
    terminal = {"FAILED", "TIMED_OUT", "CANCELLED"}
    rep.check("状态枚举包含全部终态", terminal <= statuses,
              f"缺失：{sorted(terminal - statuses)}")


def check_06_artifact_digest_is_real(schemas: dict, rep: Report) -> None:
    """样例中记录的摘要必须能对应到真实文件。

    数值样例最容易出的问题是「看起来合理但已过期」——文件改一个字，摘要就失效，
    而样例本身不会报错。这里把它变成一条可执行的检查。

    覆盖全部逐字节引用的交接物：环境生成服务的 Dockerfile、镜像引用与修复服务的
    补丁、固定 MD 报告。交接物的摘要不实，下游的完整性核验就是空转。
    """
    print("\n检查 06：产物样例的摘要与实际文件一致")

    cases = [
        ("artifact.json", ROOT / "fixtures" / "draft" / "docker" / "Dockerfile.ok"),
        ("artifact.patch.json", ROOT / "fixtures" / "mdfixer" / "reference.patch"),
        ("artifact.image-ref-001.json", ROOT / "fixtures" / "draft" / "docker" / "image-ref.txt"),
        ("artifact.image-ref-002.json", ROOT / "fixtures" / "mdfixer" / "docker" / "image-ref.txt"),
        ("artifact.error-report-003.json", ROOT / "fixtures" / "mdfixer" / "error-report.json"),
    ]

    for name, target in cases:
        if not target.exists():
            rep.check(f"{name} 对应文件存在", False, f"未找到：{target}")
            continue

        art = sample(name)
        raw = target.read_bytes()
        rep.check(f"{name} 的 sha256 与文件内容一致",
                  art.get("sha256") == hashlib.sha256(raw).hexdigest(),
                  f"样例记录 {art.get('sha256')}\n实际摘要 {hashlib.sha256(raw).hexdigest()}")
        rep.check(f"{name} 的 size_bytes 与文件大小一致",
                  art.get("size_bytes") == len(raw),
                  f"样例记录 {art.get('size_bytes')}，实际 {len(raw)}")


def check_07_error_codes_documented(schemas: dict, rep: Report) -> None:
    """样例中出现的每个错误码，都必须在 error-codes.md 中有定义。

    错误码文档与样例分处两个文件，极易各自演化：加了新码写了样例却忘了写文档，
    或文档里列了码却没有样例支撑。这里把两者绑在一起。
    """
    print("\n检查 07：样例用到的错误码均已归档")

    doc = CONTRACTS / "error-codes.md"
    if not doc.exists():
        rep.check("error-codes.md 存在", False, f"未找到：{doc}")
        return
    doc_text = doc.read_text(encoding="utf-8")

    used: dict[str, list[str]] = {}
    for path in sorted(SAMPLES.glob("*.json")):
        obj = load_json(path)
        if isinstance(obj, dict):
            code = (obj.get("error") or {}).get("code")
            if code:
                used.setdefault(code, []).append(path.name)

    rep.check("样例覆盖至少一个错误码", bool(used), "没有任何样例包含 error.code")

    for code, files in sorted(used.items()):
        rep.check(f"{code} 已在 error-codes.md 中定义",
                  code in doc_text,
                  f"被 {', '.join(files)} 使用，但文档中查无此码")


def check_08_success_invariants(schemas: dict, rep: Report) -> None:
    """成功任务的输出必须自洽。"""
    print("\n检查 08：成功任务的输出不变式")

    job = sample("job.succeeded.json")
    result = (job.get("output") or {}).get("result") or {}
    for flag in ("build_ok", "artifact_present", "verify_ok"):
        rep.check(f"DRAFT SUCCEEDED 蕴含 result.{flag} 为真",
                  result.get(flag) is True, f"实际 {result.get(flag)!r}")

    iters = (job.get("output") or {}).get("iterations") or []
    last = iters[-1].get("outcome") if iters else None
    rep.check("DRAFT 成功任务的最后一轮不以失败告终",
              bool(iters) and last in {"BUILD_OK", "VERIFY_OK"},
              f"最后一轮 outcome = {last!r}")

    # REPAIR：采纳了补丁与未采纳任何补丁是两种合法终态，各有各的不变式。
    # 「全部候选都被拒」不是失败——任务仍是 SUCCEEDED，见 contracts/error-codes.md 第 6 节。
    for name in ("job.repair.succeeded.json", "job.repair.no_fix.json"):
        out = (sample(name).get("output") or {})
        fixed = out.get("fixed") or []
        rejected = out.get("rejected") or []
        ver = out.get("verification") or {}

        if fixed:
            for flag in ("build_ok", "test_ok", "recheck_ok"):
                rep.check(f"{name} 采纳了补丁，蕴含 verification.{flag} 为真",
                          ver.get(flag) is True, f"实际 {ver.get(flag)!r}")
        else:
            rep.check(f"{name} 未采纳任何补丁时，必须留下拒绝理由",
                      bool(rejected),
                      "fixed 为空且 rejected 也为空：这次修复什么都没说明")
            rep.check(f"{name} 未采纳任何补丁时，不得给出补丁引用",
                      "patch_artifact_id" not in out,
                      f"实际 patch_artifact_id={out.get('patch_artifact_id')!r}")


def check_09_repair_consumes_missing_only(schemas: dict, rep: Report) -> None:
    """修复只针对缺失依赖。

    报告本身可以同时包含 MISSING 与 REDUNDANT（检测服务一次给出全部发现），
    但修复请求必须把范围限定为 MISSING——请求修复冗余依赖应被拒绝。
    """
    print("\n检查 09：修复任务只消费缺失依赖报告")

    req = sample("create-repair-job.request.json")
    rep.check("修复请求的 finding_type 为 MISSING",
              req["input"]["report"].get("finding_type") == "MISSING",
              f"实际 {req['input']['report'].get('finding_type')!r}")

    bad = copy.deepcopy(req)
    bad["input"]["report"]["finding_type"] = "REDUNDANT"
    rep.check("请求修复冗余依赖被拒绝",
              bool(errors_of(schemas["job-input-repair"], bad["input"])),
              "未被拒绝：修复只针对缺失依赖")

    # 报告样例同时含两类发现，用于验证「修复只消费其中一类」
    rep_obj = sample("error-report.json")
    kinds = {f.get("type") for f in rep_obj.get("findings", [])}
    rep.check("报告样例覆盖 MISSING 与 REDUNDANT 两类",
              kinds == {"MISSING", "REDUNDANT"},
              f"实际 {sorted(kinds)}")

    provs = {f.get("provenance") for f in rep_obj.get("findings", [])}
    rep.check("报告样例区分工具来源与人工来源",
              "TOOL" in provs and provs & {"INSTRUCTOR_ORACLE", "MANUAL"},
              f"实际 {sorted(p for p in provs if p)}")


def check_10_repair_failure_boundary(schemas: dict, rep: Report) -> None:
    """修复的成败边界：什么时候写 rejected[]，什么时候写 job.error。

    这条线最容易画错——「所有候选都被拒绝」与「修复任务失败」看起来是一回事，
    实际不是：前者是修复服务的结论，后者是修复服务没跑完。混同的后果是调度器
    把一次带着明确理由的结论当成基础设施故障重跑。
    """
    print("\n检查 10：修复的失败边界与错误码")

    stages = set(schemas["task"]["$defs"]["error"]["properties"]["stage"]["enum"])
    rep.check("task.schema.json 的 error.stage 覆盖 REPAIR 阶段",
              "REPAIR" in stages, f"实际 {sorted(stages)}")

    doc = (CONTRACTS / "error-codes.md").read_text(encoding="utf-8")
    for code in ("REPAIR_6001", "REPAIR_6002"):
        rep.check(f"{code} 已在 error-codes.md 中定义", code in doc, "文档中查无此码")

    job = sample("job.failed.repair6001.json")
    errs = errors_of(schemas["task"], job)
    rep.check("job.failed.repair6001.json 符合统一任务模型 2.0",
              not errs, "\n".join(errs))
    rep.check("修复输入不可用时任务为 FAILED 且 error.stage 为 REPAIR",
              job.get("status") == "FAILED"
              and (job.get("error") or {}).get("stage") == "REPAIR",
              f"实际 status={job.get('status')!r} "
              f"stage={(job.get('error') or {}).get('stage')!r}")

    # 报告版本 vs 请求版本：schema 只能校验格式，一致性由 mock 的 cross_checks 拦。
    # 样例必须走「声明 report.commit」这条路，否则这个约束在样例里不可见。
    req = sample("create-repair-job.request.json")["input"]
    declared = req["report"].get("commit")
    rep.check("REPAIR 请求样例声明了 report.commit",
              bool(declared),
              "未声明：报告版本只能在执行阶段取回报告后才发现不符")
    rep.check("REPAIR 请求样例的 report.commit 与 repository.commit 一致",
              declared == req["repository"]["commit"],
              f"report.commit={declared!r}\n"
              f"repository.commit={req['repository']['commit']!r}")


def check_11_repair_fixture_is_real(schemas: dict, rep: Report) -> None:
    """修复的固定输入必须与磁盘上的真实文件对得上。

    报告说「Makefile 第 9 行的 main.o 规则缺 config.h」，那就得真有这么一个文件、
    真有这么一行、补丁也真能补上它。否则这份「固定输入」只是一段看起来合理的文本，
    谁也复核不了——而这正是 error-report.schema.json 要求每条发现都带 location 与
    evidence 的理由。位置与证据既然写了，就该能被验证。
    """
    print("\n检查 11：修复固定输入与磁盘文件一致")

    fx = ROOT / "fixtures" / "mdfixer"
    makefile = fx / "Makefile"
    if not makefile.exists():
        rep.check("固定输入项目存在", False, f"未找到：{makefile}")
        return

    report = load_json(fx / "error-report.json")
    errs = errors_of(schemas["error-report"], report)
    rep.check("固定 MD 报告符合 error-report.schema.json", not errs, "\n".join(errs))

    lines = makefile.read_text(encoding="utf-8").splitlines()
    for finding in report["findings"]:
        dep = finding["dependency"]
        loc = finding["location"]

        target_file = fx / loc["file"]
        rep.check(f"发现 {dep} 的 location.file 指向真实文件", target_file.exists(),
                  f"未找到 {target_file}")

        line = loc.get("line")
        ok = False
        if target_file.exists() and line:
            idx = line - 1
            ok = 0 <= idx < len(lines) and lines[idx].startswith(finding["target"] + ":")
        rep.check(f"发现 {dep} 的 location.line 指向 {finding['target']} 的规则行",
                  ok,
                  f"第 {line} 行实际为 {lines[line - 1]!r}"
                  if line and 0 <= line - 1 < len(lines) else f"无法定位第 {line} 行")

    # 发现的类型必须与源码对得上：MISSING 是说「读取了但没声明」，
    # REDUNDANT 是说「声明了但从没读取」。两个断言都能在源码里数一遍——
    # 一条发现若连这两点都对不上，它的 evidence 就是写来好看的。
    src = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted(fx.glob("*.c")) + sorted(fx.glob("*.h"))
    )
    included = set(re.findall(r'#include\s+"([^"]+)"', src))

    for finding in report["findings"]:
        dep = finding["dependency"]
        if finding["type"] == "MISSING":
            rep.check(f"MISSING {dep} 确实被源码 include（不是凭空的缺失）",
                      dep in included,
                      f"源码里没有任何文件 include {dep}")
        else:
            rep.check(f"REDUNDANT {dep} 确实没被任何源码 include（不是误判的冗余）",
                      dep not in included,
                      f"{dep} 其实被 include 了，不该报为冗余")

    provs = {f["provenance"] for f in report["findings"]}
    rep.check("固定 MD 报告全部标为人工来源（标准答案不混入工具产出）",
              bool(provs) and provs <= {"INSTRUCTOR_ORACLE", "MANUAL"},
              f"实际 {sorted(provs)}")

    req = sample("create-repair-job.request.json")["input"]
    rep.check("固定 MD 报告的 commit 与 REPAIR 请求样例一致",
              report["repository"]["commit"] == req["repository"]["commit"],
              f"报告 {report['repository']['commit']}\n请求 {req['repository']['commit']}")

    # 报告与 Makefile 的路径基准由环境约定给出，不再是任务里的 project_subdir：
    # 报告说 location.file=Makefile，就该在 environment.project_root 下真有这个文件。
    env = sample_environments().get(req.get("environment_id"))
    rep.check("REPAIR 请求样例的 environment_id 有可查环境记录",
              env is not None, f"实际 {req.get('environment_id')!r}")
    rep.check("该环境的项目根指向固定输入目录",
              bool(env) and env["project_root"].rstrip("/").endswith("fixtures/mdfixer"),
              f"实际 {env and env['project_root']!r}")

    # 参考补丁：模拟最小应用，验证两件事——补丁改动行确实存在于 Makefile，
    # 以及应用之后缺失依赖被补上、冗余声明原样保留。
    patch_text = (fx / "reference.patch").read_text(encoding="utf-8")
    removed = [l[1:] for l in patch_text.splitlines()
               if l.startswith("-") and not l.startswith("---")]
    added = [l[1:] for l in patch_text.splitlines()
             if l.startswith("+") and not l.startswith("+++")]

    before = makefile.read_text(encoding="utf-8")
    after = before
    applicable = bool(removed) and len(removed) == len(added)
    for old, new in zip(removed, added):
        if old not in after:
            applicable = False
            break
        after = after.replace(old, new, 1)

    rep.check("参考补丁的改动行在 Makefile 中原样存在（可干净应用）", applicable,
              "补丁与 Makefile 不匹配，git apply 会失败")

    rule_after = [l for l in after.splitlines() if l.startswith("main.o:")]
    for finding in report["findings"]:
        dep = finding["dependency"]
        if finding["type"] == "MISSING":
            rep.check(f"应用参考补丁后，main.o 规则已声明 {dep}",
                      any(dep in l for l in rule_after),
                      f"实际规则：{rule_after}")
        else:
            rep.check(f"应用参考补丁后，冗余声明 {dep} 原样保留",
                      any(dep in l for l in rule_after),
                      f"修复只针对缺失依赖，不得改动 {dep}。实际规则：{rule_after}")

    art = sample("artifact.patch.json")
    rep.check("PATCH 产物样例的 type 为 PATCH", art.get("type") == "PATCH",
              f"实际 {art.get('type')!r}")


def check_12_detection_outputs(schemas: dict, rep: Report) -> None:
    """检测服务的输出样例必须符合专用契约和版本约束。"""
    print("\n检查 12：检测服务输出契约")

    full = sample("job.full-check.succeeded.json")
    incr = sample("job.incremental-check.succeeded.json")
    for job in (full, incr):
        schema_name = OUTPUT_SCHEMA[job["job_type"]]
        errs = errors_of(schemas[schema_name], job["output"])
        rep.check(f"{job['job_type']} 成功输出符合专有契约", not errs, "\n".join(errs))

    failed = sample("job.failed.analysis5001.json")
    rep.check("ANALYSIS_5001 样例是 检测任务的分析阶段失败",
              failed.get("job_type") in {"FULL_CHECK", "INCREMENTAL_CHECK"}
              and failed.get("status") == "FAILED"
              and failed.get("error", {}).get("code") == "ANALYSIS_5001"
              and failed.get("error", {}).get("stage") == "ANALYSIS"
              and "output" not in failed,
              "分析器失败必须是 FAILED/ANALYSIS_5001，且不得携带 output")

    rep.check("增量输出的 base_commit 与输入一致",
              incr["output"]["base_commit"] == incr["input"]["base_commit"],
              "输出不能把基线归属到另一提交")
    rep.check("增量输出的 environment_id 与输入一致",
              incr["output"]["environment_id"] == incr["input"]["environment_id"],
              "环境不同则结果不可比较")

    rep.check("全量输出的 resolved_commit 与输入一致",
              full["output"]["resolved_commit"] == full["input"]["repository"]["commit"],
              "全量产物不能归属到另一提交")
    rep.check("增量输出的 resolved_commit 与当前输入一致",
              incr["output"]["resolved_commit"] == incr["input"]["repository"]["commit"],
              "增量产物不能归属到另一提交")

    records = {}
    for record_name, content_path, content_schema in DETECTION_ARTIFACTS:
        record = sample(record_name)
        content = load_json(content_path)
        records[record["artifact_id"]] = record

        errs = errors_of(schemas[content_schema], content)
        rep.check(f"{content_path.relative_to(ROOT)} 符合 {content_schema} 契约",
                  not errs, "\n".join(errs))

        raw = content_path.read_bytes()
        actual_digest = hashlib.sha256(raw).hexdigest()
        rep.check(f"{record_name} 的摘要和大小与实物一致",
                  record.get("sha256") == actual_digest and record.get("size_bytes") == len(raw),
                  f"记录 sha256={record.get('sha256')} size={record.get('size_bytes')}\n"
                  f"实物 sha256={actual_digest} size={len(raw)}")
        rep.check(f"{record_name} 的提交与实物内容一致",
                  record.get("source_commit") == content["repository"]["commit"],
                  "artifact source_commit 必须等于图或报告的 repository.commit")

    full_ids = {
        full["output"]["actual_graph_artifact_id"],
        full["output"]["declared_graph_artifact_id"],
        full["output"]["error_report_artifact_id"],
    }
    incr_ids = {incr["output"]["error_report_artifact_id"]}
    rep.check("FULL_CHECK 输出的三个 artifact ID 都有可读取记录",
              full_ids <= records.keys(), f"缺少 {sorted(full_ids - records.keys())}")
    rep.check("INCREMENTAL_CHECK 输出的错误报告 artifact ID 有可读取记录",
              incr_ids <= records.keys(), f"缺少 {sorted(incr_ids - records.keys())}")
    rep.check("增量输入引用 FULL_CHECK 的实际图 Artifact ID",
              incr["input"]["baseline"]["actual_graph_artifact_id"] ==
              full["output"]["actual_graph_artifact_id"],
              "baseline.actual_graph_artifact_id 必须引用前一次全量检测的实际图")
    rep.check("EChecker 不产出 ACTUAL_GRAPH",
              "actual_graph_artifact_id" not in incr["output"],
              "contract-phase 的产出方矩阵只允许 BuildChecker 产出 ACTUAL_GRAPH")

    base_commit = full["output"]["resolved_commit"]
    current_commit = incr["output"]["resolved_commit"]
    for label, commit in (("基线", base_commit), ("当前", current_commit)):
        proc = subprocess.run(
            ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        rep.check(f"依赖检测服务 {label} SHA 是仓库中真实存在的提交",
                  len(commit) == 40 and proc.returncode == 0,
                  f"无法解析提交 {commit}")

    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", base_commit, current_commit],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    rep.check("依赖检测服务增量基线是当前提交的祖先",
              ancestry.returncode == 0,
              f"{base_commit} 不是 {current_commit} 的祖先")


def check_13_contract_phase_alignment(schemas: dict, rep: Report) -> None:
    """锁定 contract-phase@431a743 已共同确定的公共约束。"""
    print("\n检查 13：contract-phase 公共结构对齐")

    rep.check("公共 Schema 与历史版本 Schema 均已加载",
              {"repository", "environment", "artifact", "task", "task-legacy-v1"}
              <= schemas.keys(),
              "缺少公共 Schema 或历史版本 Schema")

    task = schemas["task"]
    execution = task["properties"]["execution"]
    rep.check("Job 始终要求完整 execution 元数据",
              "execution" in task["required"] and
              set(execution["required"]) ==
              {"created_at", "started_at", "finished_at", "attempt"} and
              execution["properties"]["attempt"].get("minimum") == 0,
              "execution 必须始终存在，QUEUED 的 attempt 从 0 开始")

    valid_job = sample("job.full-check.succeeded.json")
    invalid_times = (
        ("不存在的日期", "2026-02-30T09:00:00.000Z"),
        ("非 UTC 偏移", "2026-09-23T17:00:00.000+08:00"),
        ("缺少毫秒", "2026-09-23T09:00:00Z"),
    )
    for label, value in invalid_times:
        bad_job = copy.deepcopy(valid_job)
        bad_job["execution"]["created_at"] = value
        rep.check(f"Job 时间拒绝{label}",
                  bool(errors_of(task, bad_job)),
                  f"未被拒绝：{value}")

    shared_codes = {"ENV_3002", "EXEC_4002", "ANALYSIS_5001",
                    "REPAIR_6001", "REPAIR_6002"}
    actual_codes = set(task["$defs"]["error"]["properties"]["code"].get("enum", []))
    rep.check("error.code 只接受共同定义的错误码", actual_codes == shared_codes,
              f"实际：{sorted(actual_codes)}")

    required_artifact = {
        "artifact_id", "type", "uri", "media_type", "producer_job_id",
        "environment_id", "sha256", "size_bytes", "created_at", "source_commit",
    }
    rep.check("Artifact 记录包含共同要求的身份、环境、校验与追溯字段",
              required_artifact <= set(schemas["artifact"]["required"]),
              f"缺少：{sorted(required_artifact - set(schemas['artifact']['required']))}")

    artifact_without_size = copy.deepcopy(sample("artifact.json"))
    artifact_without_size["size_bytes"] = None
    rep.check("Artifact 的 size_bytes 不适用时允许为 null",
              not errors_of(schemas["artifact"], artifact_without_size),
              "共同契约要求字段保留，并在不适用时取 null")
    artifact_with_bad_size = copy.deepcopy(sample("artifact.json"))
    artifact_with_bad_size["size_bytes"] = -1
    rep.check("Artifact 的 size_bytes 为整数时不得为负数",
              bool(errors_of(schemas["artifact"], artifact_with_bad_size)),
              "负数文件大小必须被拒绝")

    timestamp_instances = (
        ("Artifact.created_at", schemas["artifact"], sample("artifact.actual-graph-001.json"),
         "created_at"),
        ("DependencyGraph.generated_at", schemas["dependency-graph"],
         load_json(DETECTION_FIXTURES / "full-check" / "actual-graph.json"), "generated_at"),
        ("ErrorReport.generated_at", schemas["error-report"],
         load_json(DETECTION_FIXTURES / "full-check" / "error-report.json"), "generated_at"),
    )
    for label, schema, instance, field in timestamp_instances:
        bad_instance = copy.deepcopy(instance)
        bad_instance[field] = "2026-09-23T09:00:00Z"
        rep.check(f"{label} 要求 UTC 毫秒精度",
                  bool(errors_of(schema, bad_instance)),
                  "缺少三位毫秒的时间不应通过")

    full_req = sample("create-full-check-job.request.json")
    incr_req = sample("create-incremental-check-job.request.json")
    for name, req in (("FULL_CHECK", full_req), ("INCREMENTAL_CHECK", incr_req)):
        payload = req["input"]
        rep.check(f"{name} Repository 符合公共 Schema",
                  not errors_of(schemas["repository"], payload["repository"]),
                  "Repository 必须含原始 URL、canonical_url 和完整 40 位 SHA")
        rep.check(f"{name} 只按 environment_id 引用环境",
                  "environment_id" in payload and "environment" not in payload
                  and "build" not in payload and "configuration_id" not in json.dumps(payload),
                  "检测任务不得内嵌环境定义或继续使用 configuration_id")

    rep.check("增量基线以 Artifact ID 而非 URI 交接",
              "actual_graph_artifact_id" in incr_req["input"]["baseline"]
              and "actual_graph_uri" not in incr_req["input"]["baseline"],
              "基线应由全局 Artifact Index 定位")

    incr_out = sample("job.incremental-check.succeeded.json")["output"]
    rep.check("EChecker 输出不含 ACTUAL_GRAPH",
              "actual_graph_artifact_id" not in incr_out,
              "共同产出方矩阵只允许 BuildChecker 产出 ACTUAL_GRAPH")

    job_samples = {
        "job.succeeded.json",
        "job.running.json",
        "job.failed.env3002.json",
        "job.timed_out.exec4002.json",
        "job.repair.succeeded.json",
        "job.repair.no_fix.json",
        "job.failed.repair6001.json",
        "job.full-check.succeeded.json",
        "job.incremental-check.succeeded.json",
        "job.failed.analysis5001.json",
    }
    rep.check("四类任务的 Job 样例统一使用 schema_version=2.0",
              all(sample(name).get("schema_version") == "2.0" for name in job_samples),
              "存在仍停留在 1.0 的 Job 样例：迁移未完成")
    rep.check("Job 样例中不再出现已被 environment_id 取代的 configuration_id",
              all("configuration_id" not in json.dumps(sample(name))
                  for name in job_samples),
              "configuration_id 仅保留在 ADR 的历史记录中，不应再出现在契约样例里")
    rep.check("历史版本 Schema 仅为存档，不再有样例引用",
              "task-legacy-v1" in schemas
              and not any(sample(name).get("schema_version") == "1.0"
                          for name in job_samples),
              "legacy Schema 应保留为版本记录，但迁移完成后不应再有 1.0 样例")

def check_14_environment_handoff(schemas: dict, rep: Report) -> None:
    """环境交接：环境生成服务产出环境，下游任务只按 environment_id 引用。

    环境被拆成两处存放——任务里存编号，完整定义由环境生成服务保存并提供。
    拆开就要能对上号：样例里出现的每个 environment_id 都得有定义，环境引用的镜像
    得是真实产物，产物归属的环境也得是环境生成服务产出的那一个。任何一处对不上，
    下游拿到的就是「有编号、没环境」。
    """
    print("\n检查 14：环境交接（环境生成服务产出、下游按 ID 引用）")

    envs = sample_environments()
    rep.check("样例中存在环境记录", bool(envs), "未找到 environment.*.json")

    for env in envs.values():
        errs = errors_of(schemas["environment"], env)
        rep.check(f"环境样例 {env['environment_id']} 符合 environment.schema.json",
                  not errs, "\n".join(errs))

    image_records = {
        record["artifact_id"]: record
        for record in (sample(n) for n in ("artifact.image-ref-001.json",
                                           "artifact.image-ref-002.json"))
    }
    for env_id, env in sorted(envs.items()):
        image = image_records.get(env.get("image"))
        rep.check(f"{env_id} 的 image 指向一个 IMAGE_REF 产物",
                  bool(image) and image.get("type") == "IMAGE_REF",
                  f"image={env.get('image')!r}")
        rep.check(f"{env_id} 的镜像产物由环境生成服务产出",
                  bool(image)
                  and image.get("environment_id") is None
                  and image.get("uri", "").startswith("artifact://draft/"),
                  "环境生成服务的产物产出时环境尚不存在：environment_id 必须为 null，"
                  "存储域必须按服务命名")

    for name in ("job.succeeded.json", "job.full-check.succeeded.json",
                 "job.incremental-check.succeeded.json",
                 "job.repair.succeeded.json", "job.repair.no_fix.json",
                 "job.failed.repair6001.json"):
        env_id = ((sample(name).get("input") or {}).get("environment_id"))
        if env_id is None:
            continue
        rep.check(f"{name} 引用的环境 {env_id} 有定义", env_id in envs,
                  "引用了没有环境记录的 environment_id：下游取不到构建命令与项目根")

    draft = sample("job.succeeded.json")["output"]
    rep.check("DRAFT 输出回报 environment_id 且有对应环境记录",
              draft.get("environment_id") in envs,
              f"实际 {draft.get('environment_id')!r}")
    rep.check("DRAFT 输出的镜像产物编号与环境引用的镜像一致",
              envs.get(draft.get("environment_id", ""), {}).get("image")
              == draft.get("image_artifact_id"),
              "两处不一致时同一环境会有两个名字")
    rep.check("DRAFT 输出不再携带 configuration_id / image_ref",
              "configuration_id" not in draft and "image_ref" not in draft,
              "旧字段已由 environment_id 与镜像产物编号取代")

    detect_env = envs.get("env-draft-fixture-mode0", {})
    rep.check("检测所用环境声明了 ptrace 运行能力",
              "ptrace" in detect_env.get("runtime_capabilities", []),
              "运行能力是需求方（依赖检测需要读文件访问记录）提出的，必须由环境声明")

    repair = sample("create-repair-job.request.json")["input"]
    rep.check("REPAIR 输入不内嵌环境定义、也不带 project_subdir",
              "environment" not in repair and "project_subdir" not in repair,
              "构建方式只在环境里表达；路径基准取环境的项目根")

    report_ref = repair["report"]
    report_record = sample("artifact.error-report-003.json")
    rep.check("REPAIR 的报告编号有对应的产物记录",
              report_ref.get("artifact_id") == report_record["artifact_id"],
              f"实际 {report_ref.get('artifact_id')!r}")
    rep.check("REPAIR 声明的 artifact_uri 与产物记录的 uri 一致",
              report_ref.get("artifact_uri") == report_record["uri"],
              f"请求 {report_ref.get('artifact_uri')!r}\n记录 {report_record['uri']!r}")
    rep.check("报告产物归属的环境就是修复所用的环境",
              report_record.get("environment_id") == repair.get("environment_id"),
              "环境不一致时复构建与复检的结论不可比")

    # 存储域是接口的一部分：按服务命名，人工基线样例用 oracle 域。域不统一时
    # 「按域映射本地目录」这类约定立刻失效，所以把取值集合钉死在契约里。
    domains = {"draft", "mdfixer", "buildchecker", "echecker", "oracle"}
    for path in sorted(SAMPLES.glob("artifact*.json")):
        record = load_json(path)
        domain = record["uri"].split("//", 1)[-1].split("/", 1)[0]
        rep.check(f"{path.name} 的存储域已登记（{domain}）", domain in domains,
                  f"未登记的存储域：{domain}；新增域必须先写进契约与文档")
    rep.check("人工基线样例用 oracle 域（不冒充工具产出）",
              sample("artifact.error-report-003.json")["uri"].startswith("artifact://oracle/")
              and {f["provenance"] for f in
                   load_json(ROOT / "fixtures" / "mdfixer" / "error-report.json")["findings"]}
              <= {"INSTRUCTOR_ORACLE", "MANUAL"},
              "人工样例的域与人工资质必须对得上")

    rep.check("修复的报告引用指向 ERROR_REPORT 产物",
              report_record["type"] == "ERROR_REPORT",
              f"实际 {report_record['type']!r}——修复的输入只能是一份依赖问题报告")

    # 成功路径的版本一致性：报告、请求与产物记录必须指向同一提交
    # （mock 在执行阶段取回报告后按同一规则判定 REPAIR_6001）
    for name in ("create-repair-job.request.json", "job.repair.succeeded.json",
                 "job.repair.no_fix.json"):
        payload = sample(name)["input"]
        rep.check(f"{name} 的请求版本与报告所依据的版本一致",
                  payload["repository"]["commit"] == report_record["source_commit"],
                  f"请求提交 {payload['repository']['commit']}；"
                  f"报告依据 {report_record['source_commit']}")
        rep.check(f"{name} 声明了复检命令",
                  bool(payload["verification"].get("recheck_command")),
                  "recheck_ok 是「补丁是否真的消除依赖问题」的唯一依据，必须执行复检")

    patch = sample("artifact.patch.json")
    rep.check("修复产物记录了所属环境",
              patch.get("environment_id") in envs,
              f"实际 {patch.get('environment_id')!r}")
    rep.check("环境生成服务的产物样例 environment_id 为 null",
              sample("artifact.json").get("environment_id") is None,
              "DOCKERFILE 产出时环境尚不存在")

    # 报告所依据的提交必须等于该环境产出时的提交：报告说「在某个环境里发现了问题」，
    # 那就得是针对这个环境构建的那份源码说的，否则位置与证据都指向别的版本。
    report_cases = (
        ("契约样例报告", sample("error-report.json"), "env-draft-fixture-mode0"),
        ("固定 MD 报告",
         load_json(ROOT / "fixtures" / "mdfixer" / "error-report.json"),
         "env-draft-mdfixer-001"),
    )
    for label, report_obj, env_id in report_cases:
        image = image_records[envs[env_id]["image"]]
        rep.check(f"{label}所依据的提交等于该环境产出时的提交",
                  report_obj["repository"]["commit"] == image["source_commit"],
                  f"报告 {report_obj['repository']['commit']}\n"
                  f"环境镜像产物 {image['source_commit']}")

    checked = {
        "DRAFT 输入提交": sample("job.succeeded.json")["input"]["repository"]["commit"],
        "DRAFT 输出提交": sample("job.succeeded.json")["output"]["resolved_commit"],
        "DOCKERFILE 产物提交": sample("artifact.json")["source_commit"],
        "PATCH 产物提交": sample("artifact.patch.json")["source_commit"],
        "报告产物提交": report_record["source_commit"],
        "REPAIR 输入提交": repair["repository"]["commit"],
    }
    for label, commit in sorted(checked.items()):
        proc = subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"],
                              cwd=ROOT, capture_output=True, text=True, check=False)
        rep.check(f"生成与修复侧样例提交真实存在：{label}",
                  len(commit) == 40 and proc.returncode == 0,
                  f"无法解析提交 {commit}")

    failed = sample("job.failed.repair6001.json")["input"]
    rep.check("REPAIR_6001 样例的请求提交确实不同于报告所依据的提交",
              failed["repository"]["commit"] != report_record["source_commit"],
              "两者相同时该失败样例不成立")


# -------------------------------------------------------------------- main

def main() -> int:
    print("接口契约校验")
    print(f"契约目录：{CONTRACTS}")
    print("=" * 68)

    schemas = load_schemas()
    print(f"已加载 schema：{', '.join(sorted(schemas))}")

    rep = Report()
    check_01_all_types_pass(schemas, rep)
    check_02_unknown_job_type_rejected(schemas, rep)
    check_03_missing_required_rejected(schemas, rep)
    check_04_error_semantics(schemas, rep)
    check_05_consistency(schemas, rep)
    check_06_artifact_digest_is_real(schemas, rep)
    check_07_error_codes_documented(schemas, rep)
    check_08_success_invariants(schemas, rep)
    check_09_repair_consumes_missing_only(schemas, rep)
    check_10_repair_failure_boundary(schemas, rep)
    check_11_repair_fixture_is_real(schemas, rep)
    check_12_detection_outputs(schemas, rep)
    check_13_contract_phase_alignment(schemas, rep)
    check_14_environment_handoff(schemas, rep)
    return rep.summary()


if __name__ == "__main__":
    sys.exit(main())
