#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""接口契约校验：DRAFT 任务。

把接口约定的最小检查固化成可重复执行的命令：

  01  有效样例通过
  02  job_type 改成 ABC，应被拒绝
  03  删除 DRAFT 的必填输入，应被拒绝
  04  系统错误与正常结果不得混淆（检出依赖问题 != 执行失败）
  05  跨 schema 枚举一致性，防止契约漂移
  06  样例中记录的产物摘要与实际文件一致

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


# ---------------------------------------------------------------- 基础设施

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_schemas() -> dict:
    suffix = ".schema.json"
    schemas = {}
    for path in sorted(CONTRACTS.glob(f"*{suffix}")):
        schemas[path.name[: -len(suffix)]] = load_json(path)
    return schemas


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
            if detail:
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
    """返回人类可读的错误列表，空列表表示通过。"""
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


# ------------------------------------------------------------------ 检查项

def check_01_valid_samples_pass(schemas: dict, rep: Report) -> None:
    print("\n检查 01：有效样例通过")

    req = sample("create-dockerfile-job.request.json")
    errs = errors_of(schemas["job-create-request"], req)
    rep.check("创建请求符合 job-create-request.schema.json", not errs, "\n".join(errs))

    errs = errors_of(schemas["job-input-draft"], req["input"])
    rep.check("DRAFT 输入符合 job-input-draft.schema.json", not errs, "\n".join(errs))

    ok_job = sample("job.succeeded.json")
    errs = errors_of(schemas["task"], ok_job)
    rep.check("成功任务符合 task.schema.json", not errs, "\n".join(errs))

    errs = errors_of(schemas["job-output-draft"], ok_job["output"])
    rep.check("DRAFT 输出符合 job-output-draft.schema.json", not errs, "\n".join(errs))

    bad_job = sample("job.failed.env3002.json")
    errs = errors_of(schemas["task"], bad_job)
    rep.check("失败任务符合 task.schema.json", not errs, "\n".join(errs))

    art = sample("artifact.json")
    errs = errors_of(schemas["artifact"], art)
    rep.check("产物记录符合 artifact.schema.json", not errs, "\n".join(errs))

    resp = sample("create-job.response.202.json")
    rep.check(
        "202 响应含 job_id / status=QUEUED",
        resp.get("status") == "QUEUED" and bool(resp.get("job_id")),
        f"实际：status={resp.get('status')!r} job_id={resp.get('job_id')!r}",
    )


def check_02_unknown_job_type_rejected(schemas: dict, rep: Report) -> None:
    print("\n检查 02：未知 job_type 必须被拒绝")

    for bad in ("ABC", "draft", ""):
        req = copy.deepcopy(sample("create-dockerfile-job.request.json"))
        req["job_type"] = bad
        errs = errors_of(schemas["job-create-request"], req)
        rep.check(
            f"job_type={bad!r} 被拒绝",
            bool(errs),
            "未被拒绝：schema 接受了非法 job_type",
        )

    # job_id 必须由服务端产生，客户端携带即为非法
    req = copy.deepcopy(sample("create-dockerfile-job.request.json"))
    req["job_id"] = "job-client-supplied"
    errs = errors_of(schemas["job-create-request"], req)
    rep.check("请求携带 job_id 被拒绝", bool(errs), "未被拒绝：客户端不应决定 job_id")


def check_03_missing_required_input_rejected(schemas: dict, rep: Report) -> None:
    print("\n检查 03：DRAFT 必填输入缺失必须被拒绝")

    cases = [
        ("删除 build.command", lambda i: i["build"].pop("command")),
        ("删除 build.verify_command", lambda i: i["build"].pop("verify_command")),
        ("删除 build 整块", lambda i: i.pop("build")),
        ("删除 limits（超时与轮数无约束）", lambda i: i.pop("limits")),
        ("删除 limits.timeout_seconds", lambda i: i["limits"].pop("timeout_seconds")),
        ("删除 repository", lambda i: i.pop("repository")),
    ]
    for label, mutate in cases:
        req = copy.deepcopy(sample("create-dockerfile-job.request.json"))
        mutate(req["input"])
        errs = errors_of(schemas["job-input-draft"], req["input"])
        rep.check(f"{label} 被拒绝", bool(errs), "未被拒绝：必填字段缺失却通过了校验")

    # 上下文文档超过 2 个应被拒绝（对齐 DRAFT 论文的预处理约束）
    req = copy.deepcopy(sample("create-dockerfile-job.request.json"))
    req["input"]["context_documents"] = ["a.md", "b.md", "c.md"]
    errs = errors_of(schemas["job-input-draft"], req["input"])
    rep.check("context_documents 超过 2 个被拒绝", bool(errs), "未被拒绝：超出论文约定上限")

    # 删除「增量任务的 baseline」属于依赖检测服务的契约，
    # 若其样例已入库，这里会一并校验。
    inc = SAMPLES / "create-incremental-check-job.request.json"
    if inc.exists():
        req = load_json(inc)
        req["input"].pop("baseline", None)
        errs = errors_of(schemas.get("job-input-incremental", {}), req["input"])
        rep.check("删除增量任务的 baseline 被拒绝", bool(errs), "未被拒绝")
    else:
        print("  [SKIP] 增量任务样例未入库（属依赖检测服务），跳过该反例")


def check_04_error_semantics(schemas: dict, rep: Report) -> None:
    print("\n检查 04：系统错误与正常结果不得混淆")

    # SUCCEEDED 必须有 output
    job = copy.deepcopy(sample("job.succeeded.json"))
    job.pop("output")
    errs = errors_of(schemas["task"], job)
    rep.check("SUCCEEDED 缺少 output 被拒绝", bool(errs), "未被拒绝：成功任务必须交付结果")

    # FAILED 必须有 error
    job = copy.deepcopy(sample("job.failed.env3002.json"))
    job.pop("error")
    errs = errors_of(schemas["task"], job)
    rep.check("FAILED 缺少 error 被拒绝", bool(errs), "未被拒绝：失败任务必须说明原因")

    # 错误码格式
    job = copy.deepcopy(sample("job.failed.env3002.json"))
    job["error"]["code"] = "NOT_A_CODE"
    errs = errors_of(schemas["task"], job)
    rep.check("非法错误码格式被拒绝", bool(errs), "未被拒绝：错误码须形如 ENV_3002")

    # 关键语义：检出问题 != 执行失败。
    # 一个 SUCCEEDED 的任务，其 output 中允许出现发现项；这不改变其终态。
    job = copy.deepcopy(sample("job.succeeded.json"))
    job["output"]["findings"] = [
        {"type": "MISSING", "target": "main.o", "dependency": "config.h"}
    ]
    errs = errors_of(schemas["task"], job)
    rep.check(
        "SUCCEEDED 携带发现项仍然合法（检出 MD != 执行失败）",
        not errs,
        "\n".join(errs),
    )

    # 反向：SUCCEEDED 不得同时带 error
    job = copy.deepcopy(sample("job.succeeded.json"))
    job["error"] = {"code": "ENV_3002", "message": "不应出现"}
    errs = errors_of(schemas["task"], job)
    rep.check(
        "SUCCEEDED 同时携带 error 应被拒绝",
        bool(errs),
        "未被拒绝：成功与失败语义必须互斥",
    )


def check_05_enum_consistency(schemas: dict, rep: Report) -> None:
    print("\n检查 05：跨 schema 枚举一致性（防契约漂移）")

    task_types = set(schemas["task"]["properties"]["job_type"]["enum"])
    req_types = set(schemas["job-create-request"]["properties"]["job_type"]["enum"])
    rep.check(
        "job_type 枚举在 task 与 create-request 中一致",
        task_types == req_types,
        f"task={sorted(task_types)}\ncreate-request={sorted(req_types)}",
    )

    statuses = set(schemas["task"]["properties"]["status"]["enum"])
    terminal = {"FAILED", "TIMED_OUT", "CANCELLED"}
    rep.check(
        "状态枚举包含全部终态",
        terminal <= statuses,
        f"缺失：{sorted(terminal - statuses)}",
    )


# -------------------------------------------------------------------- main

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
    rep.check(
        "artifact.sha256 与文件内容一致",
        art.get("sha256") == hashlib.sha256(raw).hexdigest(),
        f"样例记录 {art.get('sha256')}\n实际摘要 {hashlib.sha256(raw).hexdigest()}",
    )
    rep.check(
        "artifact.size_bytes 与文件大小一致",
        art.get("size_bytes") == len(raw),
        f"样例记录 {art.get('size_bytes')}，实际 {len(raw)}",
    )


def main() -> int:
    print("接口契约校验：DRAFT 任务")
    print(f"契约目录：{CONTRACTS}")
    print("=" * 68)

    schemas = load_schemas()
    print(f"已加载 schema：{', '.join(sorted(schemas))}")

    rep = Report()
    check_01_valid_samples_pass(schemas, rep)
    check_02_unknown_job_type_rejected(schemas, rep)
    check_03_missing_required_input_rejected(schemas, rep)
    check_04_error_semantics(schemas, rep)
    check_05_enum_consistency(schemas, rep)
    check_06_artifact_digest_is_real(schemas, rep)
    return rep.summary()


if __name__ == "__main__":
    sys.exit(main())
