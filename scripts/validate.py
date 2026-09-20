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

用法（在仓库根目录执行）：
    python scripts/validate.py

退出码 0 表示全部通过；非 0 表示有检查失败，失败项会逐条打印。
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "contracts"
SAMPLES = CONTRACTS / "samples"

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover
    sys.exit(
        "缺少依赖 jsonschema。请先安装：\n"
        "    python -m pip install jsonschema"
    )

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
    validator = Draft202012Validator(schema)
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

    # 响应样例：按 job_type 校验专有输出
    for name in ("job.succeeded.json", "job.repair.succeeded.json"):
        job = sample(name)
        errs = errors_of(schemas["task"], job)
        rep.check(f"{name} 符合统一任务模型", not errs, "\n".join(errs))
        out_schema = OUTPUT_SCHEMA.get(job.get("job_type"))
        if out_schema:
            errs = errors_of(schemas[out_schema], job.get("output", {}))
            rep.check(f"{name} 的 {job['job_type']} 输出符合契约", not errs, "\n".join(errs))

    for name in ("job.running.json", "job.failed.env3002.json",
                 "job.timed_out.exec4002.json"):
        errs = errors_of(schemas["task"], sample(name))
        rep.check(f"{name} 符合统一任务模型", not errs, "\n".join(errs))

    errs = errors_of(schemas["artifact"], sample("artifact.json"))
    rep.check("产物记录符合 artifact.schema.json", not errs, "\n".join(errs))

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
          ("删除 environment（无从复构建）", lambda i: i.pop("environment")),
          ("删除 environment.recheck_command 之外的必填项 build_command",
           lambda i: i["environment"].pop("build_command")),
          ("删除 repository", lambda i: i.pop("repository"))]),

        ("create-full-check-job.request.json", "FULL_CHECK",
         [("删除 environment.configuration_id（基线身份不全）",
           lambda i: i["environment"].pop("configuration_id")),
          ("删除 build.clean_command", lambda i: i["build"].pop("clean_command")),
          ("删除 build.project_root", lambda i: i["build"].pop("project_root")),
          ("删除 repository.commit", lambda i: i["repository"].pop("commit"))]),

        ("create-incremental-check-job.request.json", "INCREMENTAL_CHECK",
         [("删除 baseline（无法判定可比性）", lambda i: i.pop("baseline")),
          ("删除 baseline.actual_graph_uri", lambda i: i["baseline"].pop("actual_graph_uri")),
          ("删除 baseline.commit", lambda i: i["baseline"].pop("commit")),
          ("删除 baseline.configuration_id",
           lambda i: i["baseline"].pop("configuration_id")),
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

    job = copy.deepcopy(sample("job.succeeded.json"))
    job.pop("output")
    rep.check("SUCCEEDED 缺少 output 被拒绝",
              bool(errors_of(schemas["task"], job)),
              "未被拒绝：成功任务必须交付结果")

    job = copy.deepcopy(sample("job.failed.env3002.json"))
    job.pop("error")
    rep.check("FAILED 缺少 error 被拒绝",
              bool(errors_of(schemas["task"], job)),
              "未被拒绝：失败任务必须说明原因")

    job = copy.deepcopy(sample("job.failed.env3002.json"))
    job["error"]["code"] = "NOT_A_CODE"
    rep.check("非法错误码格式被拒绝",
              bool(errors_of(schemas["task"], job)),
              "未被拒绝：错误码须形如 ENV_3002")

    # 关键语义：检出问题 != 执行失败。
    # 一次成功的检测任务，其 output 中允许承载发现项，任务终态仍是 SUCCEEDED。
    # 用检测类任务构造该场景——DRAFT 与 REPAIR 的专有输出契约中本就没有发现项。
    findings_job = {
        "schema_version": "1.0",
        "job_id": "job-full01",
        "job_type": "FULL_CHECK",
        "status": "SUCCEEDED",
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

    job = copy.deepcopy(sample("job.succeeded.json"))
    job["error"] = {"code": "ENV_3002", "message": "不应出现"}
    rep.check("SUCCEEDED 同时携带 error 应被拒绝",
              bool(errors_of(schemas["task"], job)),
              "未被拒绝：成功与失败语义必须互斥")

    # output 与 error 都是终态的产物
    for status in ("QUEUED", "RUNNING"):
        job = copy.deepcopy(sample("job.running.json"))
        job["status"] = status

        with_error = copy.deepcopy(job)
        with_error["error"] = {"code": "ENV_3002", "message": "未结束不应有 error"}
        rep.check(f"{status} 携带 error 应被拒绝",
                  bool(errors_of(schemas["task"], with_error)),
                  "未被拒绝：error 只应出现在终态")

        with_output = copy.deepcopy(job)
        with_output["output"] = copy.deepcopy(sample("job.succeeded.json")["output"])
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

    statuses = set(schemas["task"]["properties"]["status"]["enum"])
    terminal = {"FAILED", "TIMED_OUT", "CANCELLED"}
    rep.check("状态枚举包含全部终态", terminal <= statuses,
              f"缺失：{sorted(terminal - statuses)}")


def check_06_artifact_digest_is_real(schemas: dict, rep: Report) -> None:
    """样例中记录的摘要必须能对应到真实文件。

    数值样例最容易出的问题是「看起来合理但已过期」——文件改一个字，摘要就失效，
    而样例本身不会报错。这里把它变成一条可执行的检查。
    """
    print("\n检查 06：产物样例的摘要与实际文件一致")

    target = ROOT / "fixtures" / "draft" / "docker" / "Dockerfile.ok"
    art = sample("artifact.json")

    if not target.exists():
        rep.check("参考 Dockerfile 存在", False, f"未找到：{target}")
        return

    raw = target.read_bytes()
    rep.check("artifact.sha256 与文件内容一致",
              art.get("sha256") == hashlib.sha256(raw).hexdigest(),
              f"样例记录 {art.get('sha256')}\n实际摘要 {hashlib.sha256(raw).hexdigest()}")
    rep.check("artifact.size_bytes 与文件大小一致",
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

    repair = sample("job.repair.succeeded.json")
    ver = (repair.get("output") or {}).get("verification") or {}
    for flag in ("build_ok", "test_ok", "recheck_ok"):
        rep.check(f"REPAIR SUCCEEDED 蕴含 verification.{flag} 为真",
                  ver.get(flag) is True, f"实际 {ver.get(flag)!r}")

    fixed = (repair.get("output") or {}).get("fixed") or []
    rep.check("REPAIR 成功任务至少修复一处", bool(fixed), "fixed 为空")


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
    return rep.summary()


if __name__ == "__main__":
    sys.exit(main())
