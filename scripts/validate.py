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

用法（在仓库根目录执行）：
    python scripts/validate.py

退出码 0 表示全部通过；非 0 表示有检查失败，失败项会逐条打印。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
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
    for name in ("job.succeeded.json", "job.repair.succeeded.json",
                 "job.repair.no_fix.json"):
        job = sample(name)
        errs = errors_of(schemas["task"], job)
        rep.check(f"{name} 符合统一任务模型", not errs, "\n".join(errs))
        out_schema = OUTPUT_SCHEMA.get(job.get("job_type"))
        if out_schema:
            errs = errors_of(schemas[out_schema], job.get("output", {}))
            rep.check(f"{name} 的 {job['job_type']} 输出符合契约", not errs, "\n".join(errs))

    for name in ("job.running.json", "job.failed.env3002.json",
                 "job.timed_out.exec4002.json", "job.failed.repair6001.json"):
        errs = errors_of(schemas["task"], sample(name))
        rep.check(f"{name} 符合统一任务模型", not errs, "\n".join(errs))

    for name in ("artifact.json", "artifact.patch.json"):
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

    覆盖两类产物：环境生成服务产出的 Dockerfile，与修复服务产出的补丁。
    两者都是交接物，交接物的摘要不实，下游的完整性核验就是空转。
    """
    print("\n检查 06：产物样例的摘要与实际文件一致")

    cases = [
        ("artifact.json", ROOT / "fixtures" / "draft" / "docker" / "Dockerfile.ok"),
        ("artifact.patch.json", ROOT / "fixtures" / "mdfixer" / "reference.patch"),
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
    rep.check("job.failed.repair6001.json 符合统一任务模型", not errs, "\n".join(errs))
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
    rep.check("REPAIR 请求样例的 project_subdir 指向固定输入目录",
              req.get("project_subdir") == "fixtures/mdfixer",
              f"实际 {req.get('project_subdir')!r}")

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
    return rep.summary()


if __name__ == "__main__":
    sys.exit(main())
