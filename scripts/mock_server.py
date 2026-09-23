#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""四类任务的**最小可跑模拟实现**（Python 标准库 + jsonschema）。

当前阶段的产出重心是接口契约，本 mock 的作用不是替代真实服务，而是让
contracts/ 下的契约**可被实际执行验证**：契约能不能表达四类任务真实的
异步生命周期，跑一下就知道。

校验一律使用 contracts/*.schema.json，不另写校验逻辑——schema 是契约的
唯一事实来源，手写第二套判断终将与它漂移。

实现范围：
    POST /v1/dockerfile-jobs            创建环境生成任务（DRAFT）
    POST /v1/full-check-jobs            创建全量检测任务（FULL_CHECK）
    POST /v1/incremental-check-jobs     创建增量检测任务（INCREMENTAL_CHECK）
    POST /v1/repair-jobs                创建依赖修复任务（REPAIR）
    GET  /v1/jobs/{job_id}              查询任务
    GET  /v1/artifacts/{artifact_id}    下载产物

执行模拟：
    四类任务均返回符合专用输出契约的模拟结果。FULL_CHECK 与
    INCREMENTAL_CHECK 会为当前 job 动态登记依赖图和错误报告；这些结果只用于
    验证接口和产物交接，不代表真实检测器已经运行。

用法：
    python scripts/mock_server.py [--port 8080]

    curl -s -X POST http://127.0.0.1:8080/v1/dockerfile-jobs \\
         -H 'Content-Type: application/json' \\
         -d @contracts/samples/create-dockerfile-job.request.json
    curl -s http://127.0.0.1:8080/v1/jobs/<job_id>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖 jsonschema。请先安装：\n    python -m pip install jsonschema")

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "contracts"
SAMPLES = CONTRACTS / "samples"
FIXTURE_DOCKERFILE = ROOT / "fixtures" / "draft" / "docker" / "Dockerfile.ok"
STATIC_A03_ARTIFACTS = (
    ("artifact.actual-graph-001.json", ROOT / "fixtures" / "a03" / "full-check" / "actual-graph.json"),
    ("artifact.declared-graph-001.json", ROOT / "fixtures" / "a03" / "full-check" / "declared-graph.json"),
    ("artifact.error-report-001.json", ROOT / "fixtures" / "a03" / "full-check" / "error-report.json"),
    ("artifact.actual-graph-002.json", ROOT / "fixtures" / "a03" / "incremental-check" / "actual-graph.json"),
    ("artifact.error-report-002.json", ROOT / "fixtures" / "a03" / "incremental-check" / "error-report.json"),
)

SCHEMA_VERSION = "1.0"
TERMINAL = {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"}

# 端点 -> job_type
ENDPOINTS = {
    "/v1/dockerfile-jobs": "DRAFT",
    "/v1/full-check-jobs": "FULL_CHECK",
    "/v1/incremental-check-jobs": "INCREMENTAL_CHECK",
    "/v1/repair-jobs": "REPAIR",
}

# job_type -> 专有输入 schema
INPUT_SCHEMA = {
    "DRAFT": "job-input-draft",
    "FULL_CHECK": "job-input-full-check",
    "INCREMENTAL_CHECK": "job-input-incremental-check",
    "REPAIR": "job-input-repair",
}

# job_type -> 专有输出 schema。任务只有通过对应输出契约后才能标为 SUCCEEDED。
OUTPUT_SCHEMA = {
    "DRAFT": "job-output-draft",
    "FULL_CHECK": "job-output-full-check",
    "INCREMENTAL_CHECK": "job-output-incremental-check",
    "REPAIR": "job-output-repair",
}

# 内存态任务表 + 产物表。mock 不做持久化，进程退出即丢失。
JOBS: dict[str, dict] = {}
ARTIFACTS: dict[str, dict] = {}
LOCK = threading.Lock()


def load_schemas() -> dict:
    suffix = ".schema.json"
    out = {}
    for path in sorted(CONTRACTS.glob(f"*{suffix}")):
        out[path.name[: -len(suffix)]] = json.loads(path.read_text(encoding="utf-8"))
    return out


SCHEMAS = load_schemas()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_job_id() -> str:
    return "job-" + uuid.uuid4().hex[:8]


# ------------------------------------------------------------------ 输入校验

def schema_errors(schema_name: str, instance) -> list[str]:
    """按契约 schema 校验，返回人类可读的错误列表。"""
    schema = SCHEMAS.get(schema_name)
    if schema is None:
        return [f"契约缺失：{schema_name}.schema.json 未找到"]
    validator = Draft202012Validator(schema)
    out = []
    for err in sorted(validator.iter_errors(instance), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "<root>"
        out.append(f"{loc}: {err.message}")
    return out


def validate_request(body) -> list[str]:
    """校验创建请求：先通用信封，再按 job_type 校验专有输入。

    返回空列表表示通过。
    """
    errs = schema_errors("job-create-request", body)
    if errs:
        return errs

    payload = body["input"]
    errs = schema_errors(INPUT_SCHEMA[body["job_type"]], payload)
    if errs:
        return errs

    return cross_checks(body["job_type"], payload)


def cross_checks(job_type: str, payload: dict) -> list[str]:
    """schema 表达不了的语义约束。

    两个跨字段一致性检查，共同点是「谁和谁必须属于同一个版本」，静态 schema 无法表达：

    - INCREMENTAL_CHECK：历史图必须能追溯到 base commit。基线图所依据的提交与配置，
      必须与本次检测的提交与配置一致，否则这份基线不适用于当前比较。
    - REPAIR：报告必须属于当前源码版本。报告声明了 commit 时即可在受理阶段直接比对，
      不一致则以 REPAIR_6001 拒绝——补丁要按报告生成，报告版本不符时补丁必然打不上。
    """
    errs = []

    if job_type == "INCREMENTAL_CHECK":
        baseline = payload["baseline"]
        if baseline["commit"] != payload["base_commit"]:
            errs.append(
                f"baseline/commit: 基线图所依据的提交 {baseline['commit']} "
                f"与 base_commit {payload['base_commit']} 不一致"
            )
        if baseline["configuration_id"] != payload["environment"]["configuration_id"]:
            errs.append(
                f"baseline/configuration_id: 基线图配置 {baseline['configuration_id']} "
                f"与本次环境配置 {payload['environment']['configuration_id']} 不一致"
            )

    if job_type == "REPAIR":
        report = payload["report"]
        declared = report.get("commit")
        if declared and declared != payload["repository"]["commit"]:
            errs.append(
                f"report/commit: 报告所依据的提交 {declared} "
                f"与 repository.commit {payload['repository']['commit']} 不一致"
                f"（REPAIR_6001：报告失效，修复拒绝执行）"
            )

    return errs


# ------------------------------------------------------------------ 产物登记

def register_artifact(job_id, job_type, commit, blob: bytes, media_type: str) -> str:
    """登记一个产物，返回 artifact_id。"""
    type_for = {"DRAFT": "DOCKERFILE", "REPAIR": "PATCH"}
    name_for = {"DRAFT": "Dockerfile", "REPAIR": "fix.patch"}
    kind = type_for.get(job_type, "BUILD_LOG")

    art_id = kind.lower().replace("_", "") + "-" + uuid.uuid4().hex[:6]
    ARTIFACTS[art_id] = {
        "artifact_id": art_id,
        "type": kind,
        "uri": f"artifact://{job_type.lower()}/{job_id}/{name_for.get(job_type, 'log.txt')}",
        "media_type": media_type,
        "producer_job_id": job_id,
        "sha256": hashlib.sha256(blob).hexdigest(),
        "size_bytes": len(blob),
        "created_at": now_iso(),
        "source_commit": commit,
        "_blob": blob,
    }
    return art_id


def register_json_artifact(job: dict, artifact_type: str,
                           filename: str, content: dict) -> str:
    """把当前检测 job 的 JSON 产物登记到下载接口。"""
    blob = (json.dumps(content, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    art_id = artifact_type.lower().replace("_", "-") + "-" + uuid.uuid4().hex[:6]
    commit = job["input"]["repository"]["commit"]
    ARTIFACTS[art_id] = {
        "artifact_id": art_id,
        "type": artifact_type,
        "uri": f"artifact://a03/{job['job_id']}/{filename}",
        "media_type": "application/json",
        "producer_job_id": job["job_id"],
        "sha256": hashlib.sha256(blob).hexdigest(),
        "size_bytes": len(blob),
        "created_at": now_iso(),
        "source_commit": commit,
        "_blob": blob,
    }
    return art_id


def load_static_a03_artifacts() -> None:
    """登记可下载的 A03 人工契约样例，并拒绝摘要不一致的启动。"""
    for record_name, content_path in STATIC_A03_ARTIFACTS:
        record = json.loads((SAMPLES / record_name).read_text(encoding="utf-8"))
        blob = content_path.read_bytes()
        if hashlib.sha256(blob).hexdigest() != record.get("sha256"):
            raise RuntimeError(f"A03 样例摘要失效：{record_name}")
        if len(blob) != record.get("size_bytes"):
            raise RuntimeError(f"A03 样例大小失效：{record_name}")
        ARTIFACTS[record["artifact_id"]] = {**record, "_blob": blob}


# -------------------------------------------------------------- 任务生命周期

def output_for_draft(job: dict) -> dict:
    raw = FIXTURE_DOCKERFILE.read_bytes() if FIXTURE_DOCKERFILE.exists() else b""
    art_id = register_artifact(job["job_id"], "DRAFT",
                               job["input"]["repository"].get("commit", ""),
                               raw, "text/x-dockerfile")
    job["output"] = {
        "dockerfile_artifact_id": art_id,
        "image_ref": f"draft-{job['job_id']}:1.0",
        "resolved_commit": job["input"]["repository"].get("commit", ""),
        # 检测方的输入要求 commit + configuration_id 共同确定基线身份，而该标识此前
        # 没有生产者。这里由环境产出方回报，下游照抄即可，不必自行发明。
        "configuration_id": "cc-mock0",
        "iterations": [
            {
                "round": 0,
                "action": "INITIAL",
                "reason": "依据 context_documents 推断构建命令为 make，"
                          "选用 ubuntu:22.04 并显式安装 build-essential。",
                "outcome": "BUILD_OK",
            }
        ],
        "result": {
            "build_ok": True,
            "artifact_present": True,
            "verify_ok": True,
            "verify_stdout": "hello draft\n",
            "rounds_used": 0,
        },
    }


def output_for_repair(job: dict) -> dict:
    patch = (
        "--- a/Makefile\n"
        "+++ b/Makefile\n"
        "@@ -5,7 +5,7 @@\n"
        " main.o: main.c\n"
        "-\t$(CC) $(CFLAGS) -c main.c -o main.o\n"
        "+\t$(CC) $(CFLAGS) -c main.c -o main.o\n"
        "+main.o: config.h feature.h\n"
    ).encode()
    art_id = register_artifact(job["job_id"], "REPAIR",
                               job["input"]["repository"].get("commit", ""),
                               patch, "text/x-diff")
    job["output"] = {
        "patch_artifact_id": art_id,
        "resolved_commit": job["input"]["repository"].get("commit", ""),
        "fixed": [
            {"target": "main.o", "dependency": "config.h",
             "style": "TARGET", "strategy": "在该目标的依赖列表中直接追加依赖项"},
            {"target": "main.o", "dependency": "feature.h",
             "style": "TARGET", "strategy": "在该目标的依赖列表中追加依赖项"},
        ],
        "rejected": [
            {"target": "main.o", "dependency": "unused.h",
             "reason": "候选顺带删除了这条冗余声明。修复只针对缺失依赖，"
                       "越界改动不予采纳"}
        ],
        "declaration_style_note": "Makefile 使用原子依赖列表，未经宏组织；"
                                  "补丁采用直接追加方式，不引入新的宏或隐式规则。",
        "verification": {
            "build_ok": True,
            "test_ok": True,
            "recheck_ok": True,
            "recheck_stdout": "make clean && make: 未报告依赖问题\n",
        },
    }


def graph_for(job: dict, graph_kind: str) -> dict:
    """生成最小依赖图；空图明确表示 mock 未执行真实分析。"""
    payload = job["input"]
    return {
        "schema_version": SCHEMA_VERSION,
        "repository": payload["repository"],
        "configuration_id": payload["environment"]["configuration_id"],
        "graph_kind": graph_kind,
        "generated_at": now_iso(),
        "nodes": [],
        "edges": [],
    }


def report_for(job: dict, mode: str, detector: str) -> dict:
    """生成无发现的模拟报告，不把 mock 结果伪装成工具检测结论。"""
    payload = job["input"]
    return {
        "schema_version": SCHEMA_VERSION,
        "repository": payload["repository"],
        "configuration_id": payload["environment"]["configuration_id"],
        "produced_by": {
            "detector": detector,
            "job_id": job["job_id"],
            "mode": mode,
        },
        "generated_at": now_iso(),
        "summary": {"missing": 0, "redundant": 0},
        "findings": [],
    }


def output_for_full_check(job: dict) -> None:
    actual_id = register_json_artifact(
        job, "ACTUAL_GRAPH", "actual-graph.json", graph_for(job, "ACTUAL"))
    declared_id = register_json_artifact(
        job, "DECLARED_GRAPH", "declared-graph.json", graph_for(job, "DECLARED"))
    report_id = register_json_artifact(
        job, "ERROR_REPORT", "error-report.json",
        report_for(job, "FULL", "BuildChecker-MOCK"))
    payload = job["input"]
    job["output"] = {
        "resolved_commit": payload["repository"]["commit"],
        "configuration_id": payload["environment"]["configuration_id"],
        "actual_graph_artifact_id": actual_id,
        "declared_graph_artifact_id": declared_id,
        "error_report_artifact_id": report_id,
        "summary": {"missing": 0, "redundant": 0},
    }


def output_for_incremental_check(job: dict) -> None:
    actual_id = register_json_artifact(
        job, "ACTUAL_GRAPH", "actual-graph.json", graph_for(job, "ACTUAL"))
    report_id = register_json_artifact(
        job, "ERROR_REPORT", "error-report.json",
        report_for(job, "INCREMENTAL", "EChecker-MOCK"))
    payload = job["input"]
    job["output"] = {
        "base_commit": payload["base_commit"],
        "resolved_commit": payload["repository"]["commit"],
        "configuration_id": payload["environment"]["configuration_id"],
        "actual_graph_artifact_id": actual_id,
        "error_report_artifact_id": report_id,
        "changes": {"added": [], "resolved": []},
    }


def run_job(job_id: str, should_fail: bool, should_analysis_fail: bool) -> None:
    """后台线程：模拟 QUEUED -> RUNNING -> 终态。"""
    time.sleep(0.4)
    with LOCK:
        job = JOBS.get(job_id)
        if job is None or job["status"] in TERMINAL:
            return
        job["status"] = "RUNNING"
        job["execution"]["started_at"] = now_iso()

    time.sleep(1.2)

    with LOCK:
        job = JOBS.get(job_id)
        if job is None or job["status"] in TERMINAL:
            return
        job["execution"]["finished_at"] = now_iso()

        if should_fail:
            job["status"] = "FAILED"
            job["error"] = {
                "code": "ENV_3002",
                "stage": "ENV",
                "message": "镜像构建失败：基础镜像缺少 C 工具链，make 不可用。",
                "detail": "step 5/7 RUN make 退出码 127；日志末尾：/bin/sh: 1: make: not found",
                "at": now_iso(),
            }
            return

        if should_analysis_fail:
            job["status"] = "FAILED"
            job["error"] = {
                "code": "ANALYSIS_5001",
                "stage": "ANALYSIS",
                "message": "依赖分析器异常退出，未能生成可信结果。",
                "detail": "mock failure injection: repository URL ends with fail-analysis",
                "at": now_iso(),
            }
            return

        builder = {
            "DRAFT": output_for_draft,
            "FULL_CHECK": output_for_full_check,
            "INCREMENTAL_CHECK": output_for_incremental_check,
            "REPAIR": output_for_repair,
        }[job["job_type"]]
        builder(job)

        output_errors = schema_errors(OUTPUT_SCHEMA[job["job_type"]], job["output"])
        if output_errors:
            job.pop("output", None)
            job["status"] = "FAILED"
            job["error"] = {
                "code": "ANALYSIS_5001" if job["job_type"] in
                        {"FULL_CHECK", "INCREMENTAL_CHECK"} else "EXEC_4002",
                "stage": "ANALYSIS" if job["job_type"] in
                         {"FULL_CHECK", "INCREMENTAL_CHECK"} else "EXEC",
                "message": "mock 生成的任务输出不符合专用 Schema。",
                "detail": "; ".join(output_errors),
                "at": now_iso(),
            }
            return
        job["status"] = "SUCCEEDED"


# ------------------------------------------------------------------ HTTP 层

class Handler(BaseHTTPRequestHandler):
    server_version = "DevOpsMock/1.0"

    # ---- 工具方法 ----

    def _send(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_bytes(self, code: int, raw: bytes, media_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, code: int, err_code: str, message) -> None:
        if isinstance(message, list):
            message = "; ".join(message)
        self._send(code, {
            "error": {"code": err_code, "message": message, "at": now_iso()}
        })

    def log_message(self, fmt, *args):
        code = args[1] if len(args) > 1 else ""
        print(f"[mock] {self.command} {self.path} -> {code}")

    # ---- 路由 ----

    def do_POST(self):  # noqa: N802
        job_type = ENDPOINTS.get(self.path)
        if job_type is None:
            self._error(404, "EXEC_4002", f"未知端点：POST {self.path}")
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError) as exc:
            self._error(400, "EXEC_4002", f"请求体不是合法 JSON：{exc}")
            return

        if isinstance(body, dict) and body.get("job_type") not in (None, job_type):
            self._error(400, "EXEC_4002",
                        f"job_type {body.get('job_type')!r} 与端点 {self.path} "
                        f"（{job_type}）不匹配")
            return

        problems = validate_request(body)
        if problems:
            self._error(400, "EXEC_4002", problems)
            return

        job_id = new_job_id()
        repo_url = body["input"].get("repository", {}).get("url", "")
        should_fail = repo_url.rstrip("/").endswith("fail")
        should_analysis_fail = (
            job_type in {"FULL_CHECK", "INCREMENTAL_CHECK"}
            and repo_url.rstrip("/").endswith("fail-analysis")
        )

        job = {
            "schema_version": SCHEMA_VERSION,
            "job_id": job_id,
            "trace_id": body.get("trace_id", f"trace-{uuid.uuid4().hex[:8]}"),
            "job_type": job_type,
            "status": "QUEUED",
            "execution": {"created_at": now_iso(), "attempt": 1},
            "input": body["input"],
        }
        with LOCK:
            JOBS[job_id] = job

        threading.Thread(
            target=run_job,
            args=(job_id, should_fail, should_analysis_fail),
            daemon=True,
        ).start()

        self._send(202, {
            "job_id": job_id,
            "job_type": job_type,
            "status": "QUEUED",
            "trace_id": job["trace_id"],
            "created_at": job["execution"]["created_at"],
        })

    def do_GET(self):  # noqa: N802
        m = re.fullmatch(r"/v1/jobs/([A-Za-z0-9_-]+)", self.path)
        if m:
            with LOCK:
                job = JOBS.get(m.group(1))
                snapshot = json.loads(json.dumps(job)) if job else None
            if snapshot is None:
                self._error(404, "EXEC_4002", f"任务不存在：{m.group(1)}")
                return
            self._send(200, snapshot)
            return

        m = re.fullmatch(r"/v1/artifacts/([A-Za-z0-9_-]+)", self.path)
        if m:
            with LOCK:
                art = ARTIFACTS.get(m.group(1))
                snapshot = dict(art) if art else None
            if snapshot is None:
                self._error(404, "EXEC_4002", f"产物不存在：{m.group(1)}")
                return
            blob = snapshot.pop("_blob", b"")
            self._send_bytes(200, blob, snapshot["media_type"])
            return

        if self.path in ("/v1/jobs", "/healthz"):
            with LOCK:
                summary = [{"job_id": j["job_id"], "job_type": j["job_type"],
                            "status": j["status"]} for j in JOBS.values()]
            self._send(200, {"jobs": summary, "endpoints": sorted(ENDPOINTS)})
            return

        self._error(404, "EXEC_4002", f"未知端点：GET {self.path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="四类任务服务的最小模拟实现")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    load_static_a03_artifacts()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("DevOps mock 已启动（内存态，无持久化）")
    for path, jt in sorted(ENDPOINTS.items()):
        print(f"  POST http://{args.host}:{args.port}{path:<30} {jt}")
    print(f"  GET  http://{args.host}:{args.port}/v1/jobs/{{job_id}}")
    print(f"  GET  http://{args.host}:{args.port}/v1/artifacts/{{artifact_id}}")
    print("  Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
