#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DRAFT 服务的**最小可跑模拟实现**（Python 标准库，零第三方依赖）。

当前阶段的产出重心是接口契约，本 mock 的作用不是替代真实服务，而是让
contracts/ 下的契约**可被实际执行验证**：契约能不能表达一次真实的异步任务
生命周期，跑一下就知道。

实现范围：
    POST /v1/dockerfile-jobs      创建 DRAFT 任务，立即 202 + QUEUED
    GET  /v1/jobs/{job_id}        查询任务状态与结果
    GET  /v1/artifacts/{id}       下载产物（证明产物可被下游读取）

行为模拟：
    任务受理后转 RUNNING，数秒后转为终态。
    默认 SUCCEEDED；若 repository.url 以 "fail" 结尾，则模拟 ENV_3002。

用法：
    python scripts/mock_server.py [--port 8080]

    curl -s -X POST http://127.0.0.1:8080/v1/dockerfile-jobs \\
         -H 'Content-Type: application/json' \\
         -d @contracts/samples/create-dockerfile-job.request.json
    curl -s http://127.0.0.1:8080/v1/jobs/job-draft01
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DOCKERFILE = ROOT / "fixtures" / "draft" / "docker" / "Dockerfile.ok"

SCHEMA_VERSION = "1.0"

JOB_TYPES = {"DRAFT", "FULL_CHECK", "INCREMENTAL_CHECK", "REPAIR"}
TERMINAL = {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"}

# 内存态任务表 + 产物表。mock 不做持久化，进程退出即丢失。
JOBS: dict[str, dict] = {}
ARTIFACTS: dict[str, dict] = {}
LOCK = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_job_id() -> str:
    return "job-" + uuid.uuid4().hex[:8]


# ------------------------------------------------------------------ 输入校验

def validate_create_request(body: dict) -> str | None:
    """返回错误消息，None 表示通过。

    这里做的是**契约要求的最小校验**，与 contracts/ 下的 schema 保持一致。
    完整校验见 scripts/validate.py。
    """
    if not isinstance(body, dict):
        return "请求体必须是 JSON 对象"
    if "job_id" in body:
        return "job_id 由服务端产生，请求中不得携带"

    job_type = body.get("job_type")
    if job_type not in JOB_TYPES:
        return f"未知 job_type：{job_type!r}；允许值 {sorted(JOB_TYPES)}"

    payload = body.get("input")
    if not isinstance(payload, dict):
        return "缺少 input 或 input 不是对象"

    if job_type != "DRAFT":
        return f"本 mock 仅实现 DRAFT，收到 {job_type}"

    repo = payload.get("repository")
    if not isinstance(repo, dict) or not repo.get("url"):
        return "input.repository.url 必填"

    build = payload.get("build")
    if not isinstance(build, dict):
        return "input.build 必填"
    for field in ("command", "verify_command"):
        if not build.get(field):
            return f"input.build.{field} 必填"

    limits = payload.get("limits")
    if not isinstance(limits, dict):
        return "input.limits 必填"
    for field in ("max_iterations", "timeout_seconds"):
        if not isinstance(limits.get(field), int) or limits[field] < 1:
            return f"input.limits.{field} 必填且为正整数"

    docs = payload.get("context_documents")
    if docs is not None and (not isinstance(docs, list) or len(docs) > 2):
        return "input.context_documents 必须是数组且最多 2 个"

    return None


# -------------------------------------------------------------- 任务生命周期

def run_job(job_id: str, should_fail: bool) -> None:
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

        # 产物：把仓库内的参考 Dockerfile 登记成一个 artifact，
        # 用于验证「产物可被下游读取」这条交接约定。
        raw = FIXTURE_DOCKERFILE.read_bytes() if FIXTURE_DOCKERFILE.exists() else b""
        art_id = "dockerfile-" + uuid.uuid4().hex[:6]
        ARTIFACTS[art_id] = {
            "artifact_id": art_id,
            "type": "DOCKERFILE",
            "uri": f"artifact://draft/{job_id}/Dockerfile",
            "media_type": "text/x-dockerfile",
            "producer_job_id": job_id,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
            "created_at": now_iso(),
            "source_commit": job["input"]["repository"].get("commit", ""),
            "_blob": raw,
        }

        job["status"] = "SUCCEEDED"
        job["output"] = {
            "dockerfile_artifact_id": art_id,
            "image_ref": f"draft-{job_id}:1.0",
            "resolved_commit": job["input"]["repository"].get("commit", ""),
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


# ------------------------------------------------------------------ HTTP 层

class Handler(BaseHTTPRequestHandler):
    server_version = "DraftMock/1.0"

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

    def _error(self, code: int, err_code: str, message: str) -> None:
        self._send(code, {
            "error": {"code": err_code, "message": message, "at": now_iso()}
        })

    def log_message(self, fmt, *args):  # 让日志带方法+路径，便于观察
        print(f"[mock] {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

    # ---- 路由 ----

    def do_POST(self):  # noqa: N802
        if self.path != "/v1/dockerfile-jobs":
            self._error(404, "EXEC_4002", f"未知端点：POST {self.path}")
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError) as exc:
            self._error(400, "EXEC_4002", f"请求体不是合法 JSON：{exc}")
            return

        problem = validate_create_request(body)
        if problem:
            self._error(400, "EXEC_4002", problem)
            return

        job_id = new_job_id()
        should_fail = body["input"]["repository"]["url"].rstrip("/").endswith("fail")

        job = {
            "schema_version": SCHEMA_VERSION,
            "job_id": job_id,
            "trace_id": body.get("trace_id", f"trace-{uuid.uuid4().hex[:8]}"),
            "job_type": "DRAFT",
            "status": "QUEUED",
            "execution": {"created_at": now_iso(), "attempt": 1},
            "input": body["input"],
        }
        with LOCK:
            JOBS[job_id] = job

        threading.Thread(target=run_job, args=(job_id, should_fail), daemon=True).start()

        # 202：已受理，尚未完成
        self._send(202, {
            "job_id": job_id,
            "job_type": "DRAFT",
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
                summary = [
                    {"job_id": j["job_id"], "status": j["status"],
                     "job_type": j["job_type"]}
                    for j in JOBS.values()
                ]
            self._send(200, {"jobs": summary})
            return

        self._error(404, "EXEC_4002", f"未知端点：GET {self.path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="DRAFT 服务最小模拟实现")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("DRAFT mock 已启动（内存态，无持久化）")
    print(f"  POST http://{args.host}:{args.port}/v1/dockerfile-jobs")
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
