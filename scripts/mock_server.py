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
    GET  /v1/environments/{environment_id}  查询环境定义

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
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖 jsonschema。请先安装：\n    python -m pip install jsonschema")

FORMAT_CHECKER = FormatChecker()


@FORMAT_CHECKER.checks("date-time", raises=(TypeError, ValueError))
def is_real_datetime(value) -> bool:
    """拒绝仅形状像时间、但日历日期不存在或缺少时区的字符串。"""
    if not isinstance(value, str):
        return True
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.tzinfo is not None

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "contracts"
SAMPLES = CONTRACTS / "samples"
FIXTURE_DOCKERFILE = ROOT / "fixtures" / "draft" / "docker" / "Dockerfile.ok"

# 静态产物记录 -> 实物内容。启动时逐条登记，供下载接口与契约样例共用；
# 摘要或大小与实物不符即拒绝启动——样例里的数字必须真实可取。
STATIC_ARTIFACTS = (
    ("artifact.actual-graph-001.json", ROOT / "fixtures" / "detection" / "full-check" / "actual-graph.json"),
    ("artifact.declared-graph-001.json", ROOT / "fixtures" / "detection" / "full-check" / "declared-graph.json"),
    ("artifact.error-report-001.json", ROOT / "fixtures" / "detection" / "full-check" / "error-report.json"),
    ("artifact.error-report-002.json", ROOT / "fixtures" / "detection" / "incremental-check" / "error-report.json"),
    ("artifact.json", FIXTURE_DOCKERFILE),
    ("artifact.patch.json", ROOT / "fixtures" / "mdfixer" / "reference.patch"),
    ("artifact.image-ref-001.json", ROOT / "fixtures" / "draft" / "docker" / "image-ref.txt"),
    ("artifact.image-ref-002.json", ROOT / "fixtures" / "mdfixer" / "docker" / "image-ref.txt"),
    ("artifact.error-report-003.json", ROOT / "fixtures" / "mdfixer" / "error-report.json"),
)

# 静态环境记录：环境生成服务产出的样例环境，供检测与修复样例按 environment_id 引用。
STATIC_ENVIRONMENTS = (
    "environment.draft-fixture-mode0.json",
    "environment.draft-mdfixer-001.json",
)

SCHEMA_VERSION = "2.0"
JOB_SCHEMA_VERSION = {
    "DRAFT": SCHEMA_VERSION,
    "FULL_CHECK": SCHEMA_VERSION,
    "INCREMENTAL_CHECK": SCHEMA_VERSION,
    "REPAIR": SCHEMA_VERSION,
}
TERMINAL = {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"}
DETECTION_TYPES = {"FULL_CHECK", "INCREMENTAL_CHECK"}

# 产物存储域按服务命名（见 contracts/artifact.schema.json）
SERVICE_DOMAIN = {
    "DRAFT": "draft",
    "REPAIR": "mdfixer",
    "FULL_CHECK": "buildchecker",
    "INCREMENTAL_CHECK": "echecker",
}

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

# 内存态任务表 + 产物表 + 环境表。mock 不做持久化，进程退出即丢失。
JOBS: dict[str, dict] = {}
ARTIFACTS: dict[str, dict] = {}
ENVIRONMENTS: dict[str, dict] = {}
LOCK = threading.Lock()


def load_schemas() -> dict:
    suffix = ".schema.json"
    out = {}
    for path in sorted(CONTRACTS.glob(f"*{suffix}")):
        out[path.name[: -len(suffix)]] = json.loads(path.read_text(encoding="utf-8"))
    return out


SCHEMAS = load_schemas()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_job_id() -> str:
    return "job-" + uuid.uuid4().hex[:8]


SELF_REPO_URL = "https://github.com/241880199/DevOps"


class JobAbort(Exception):
    """构建器判定任务必须以失败收场时抛出；run_job 据此写入终态与错误码。"""

    def __init__(self, code: str, stage: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code, self.stage, self.message, self.detail = code, stage, message, detail


def resolve_commit(commit: str | None, repo_url: str = "") -> str | None:
    """把环境生成任务输入里的版本解析成**仓库中真实存在**的完整 40 位 SHA。

    两件事一起做，缺一不可：产物记录要求完整 SHA，而输入允许缺省或缩写；同时记进
    产物的必须是真实存在的提交——格式合法但不存在的 SHA 会让下游拿到一条永远对不上
    的追溯信息，而它在 schema 上完全合法。

    mock 没有克隆远端的能力：URL 不是本仓库时只采用完整 SHA（无法验证存在性），
    缩写与缺省一律解析失败。
    """
    is_self = repo_url.rstrip("/").removesuffix(".git") == SELF_REPO_URL
    if not is_self:
        return commit if commit and re.fullmatch(r"[0-9a-f]{40}", commit) else None
    proc = subprocess.run(["git", "rev-parse", "--verify", f"{commit or 'HEAD'}^{{commit}}"],
                          cwd=ROOT, capture_output=True, text=True, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


# ------------------------------------------------------------------ 输入校验

def schema_errors(schema_name: str, instance) -> list[str]:
    """按契约 schema 校验，返回人类可读的错误列表。"""
    schema = SCHEMAS.get(schema_name)
    if schema is None:
        return [f"契约缺失：{schema_name}.schema.json 未找到"]
    validator = Draft202012Validator(schema, format_checker=FORMAT_CHECKER)
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

    repository = payload.get("repository", {})
    if job_type == "DRAFT":
        # 版本解析是受理阶段就能判定的输入问题：解析不出来就该拒绝请求，
        # 而不是先把任务建起来、再在产出前失败（那会让错误码去挤占执行期的档位）。
        if resolve_commit(repository.get("commit"), repository.get("url", "")) is None:
            errs.append(
                f"repository/commit: {repository.get('commit')!r} 无法解析为仓库中"
                f"真实存在的提交——环境生成要按这个版本产出并回报完整 SHA"
                f"（mock 只解析本仓库的提交，外部仓库须给完整 SHA）"
            )

    if "canonical_url" in repository:
        try:
            parsed = urlsplit(repository.get("url", ""))
            invalid = (
                parsed.scheme != "https" or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or bool(parsed.query) or bool(parsed.fragment)
                or parsed.port not in (None, 443)
            )
            path = parsed.path.rstrip("/")
            if path.endswith(".git"):
                path = path[:-4]
            expected = "" if invalid else f"https://{parsed.hostname.lower()}{path}"
        except ValueError:
            expected = ""
        if not expected or repository["canonical_url"] != expected:
            errs.append(
                "repository/canonical_url: 必须等于按 contract-phase 规则规范化后的 URL"
            )

    # 任务只按 environment_id 引用环境；环境由环境生成服务产出并保存，
    # 未登记的环境说明下游拿不到完整定义，任务不该被受理。
    env_id = payload.get("environment_id")
    if env_id is not None:
        with LOCK:
            known_env = env_id in ENVIRONMENTS
        if not known_env:
            errs.append(
                f"environment_id: 环境 {env_id} 未注册。环境由环境生成服务产出，"
                f"任务只能引用已存在的环境（GET /v1/environments/{{environment_id}}）"
            )
        elif (job_type in {"FULL_CHECK", "INCREMENTAL_CHECK"}
              and "ptrace" not in ENVIRONMENTS[env_id].get("runtime_capabilities", [])):
            errs.append(
                f"environment_id: 环境 {env_id} 未声明 ptrace——依赖检测要读文件访问记录，"
                f"这项能力属于环境的需求，应在生成环境时提出，而不是假设默认具备"
            )

    if job_type == "INCREMENTAL_CHECK":
        baseline = payload["baseline"]
        if baseline["commit"] != payload["base_commit"]:
            errs.append(
                f"baseline/commit: 基线图所依据的提交 {baseline['commit']} "
                f"与 base_commit {payload['base_commit']} 不一致"
            )
        if baseline["environment_id"] != payload["environment_id"]:
            errs.append(
                f"baseline/environment_id: 基线图环境 {baseline['environment_id']} "
                f"与本次环境 {payload['environment_id']} 不一致"
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

        errs.extend(report_reference_problems(payload))

    return errs


def report_reference_problems(payload: dict) -> list[str]:
    """修复输入是否可用：取得到、是报告、属于同一环境、且确有可修的东西。

    只校验「编号存在」远远不够：编号可以指向一份 Dockerfile 或一份补丁，也可以
    指向另一个环境的报告。那样修复会在错误的输入上跑完全程，而结论看起来完全正常。
    artifact_uri 是可选的对照字段，填了必须与产物记录一致。
    """
    errs = []
    report = payload["report"]
    record = ARTIFACTS.get(report["artifact_id"])
    if record is None:
        return [
            f"report/artifact_id: 未登记产物 {report['artifact_id']}。"
            f"报告必须先可经 GET /v1/artifacts/{{artifact_id}} 取回"
        ]

    if record.get("type") != "ERROR_REPORT":
        errs.append(
            f"report/artifact_id: 产物 {report['artifact_id']} 的类型是 "
            f"{record.get('type')!r}，不是 ERROR_REPORT——修复的输入只能是一份依赖问题报告"
        )
    if record.get("environment_id") != payload.get("environment_id"):
        errs.append(
            f"report/artifact_id: 报告所属环境 {record.get('environment_id')!r} "
            f"与本次环境 {payload.get('environment_id')!r} 不一致——"
            f"不同环境下的依赖结论不可比"
        )
    if report.get("artifact_uri") and report["artifact_uri"] != record.get("uri"):
        errs.append(
            f"report/artifact_uri: {report['artifact_uri']} "
            f"与产物记录的 uri {record.get('uri')} 不一致"
        )
    doc, body_problems = report_body(record)
    errs.extend(f"report/artifact_id: {p}" for p in body_problems)
    if doc is not None:
        # 环境要看**报告正文**声明的那个：产物记录的元数据可能被错标或调换，
        # 只比元数据的话，一份来自别环境的报告会一路通过并驱动一次错误的修复。
        if doc.get("environment_id") != payload.get("environment_id"):
            errs.append(
                f"report/artifact_id: 报告正文声明的环境 {doc.get('environment_id')!r} "
                f"与本次环境 {payload.get('environment_id')!r} 不一致——"
                f"不同环境下的依赖结论不可比"
            )
        declared_commit = doc["repository"]["commit"]
        if record.get("source_commit") != declared_commit:
            errs.append(
                f"report/artifact_id: 报告内容与产物记录不一致——记录的 source_commit "
                f"{record.get('source_commit')!r} 与报告里的 repository.commit "
                f"{declared_commit!r} 不符；记录的元数据不可信时，版本比对就是空转"
            )
        if not [f for f in doc["findings"] if f.get("type") == "MISSING"]:
            errs.append(
                "报告里没有 MISSING 发现——没有可修复的目标时「修复」无意义"
                "（REPAIR_6001）"
            )
    return errs


def report_body(record: dict):
    """取回报告内容并**按契约校验**。返回 (文档, 问题列表)。

    内容解析不了、或不符合 error-report 契约，都是「输入不可用」，必须拒绝：把
    「解析失败」当成「没问题」，等于让一份字节错误的报告驱动一次补丁生成。
    """
    raw = record.get("_blob")
    if not raw:
        return None, ["报告内容读取不到（产物记录没有可下载的内容）"]
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return None, [f"报告内容不是合法 JSON：{exc}"]
    problems = schema_errors("error-report", doc)
    if problems:
        # 结构不合契约时**不返回文档**：否则调用方会直接去取 repository / findings，
        # 遇到 `{}` 这类输入就抛 KeyError，一个输入错误变成 500。
        return None, [f"报告内容不符合 error-report 契约：{p}" for p in problems]
    return doc, []


# ------------------------------------------------------------------ 产物登记

def register_artifact(job_id: str, job_type: str, commit: str, blob: bytes,
                      media_type: str, *, artifact_type: str, filename: str,
                      environment_id=None) -> str:
    """登记一个产物，返回 artifact_id。

    environment_id 为 None 表示产物产出时环境尚不存在——环境生成服务自己的产物
    属于这种情形，见 contracts/artifact.schema.json 的对应约束。
    """
    art_id = artifact_type.lower().replace("_", "-") + "-" + uuid.uuid4().hex[:6]
    record = {
        "artifact_id": art_id,
        "type": artifact_type,
        "uri": f"artifact://{SERVICE_DOMAIN[job_type]}/{job_id}/{filename}",
        "media_type": media_type,
        "producer_job_id": job_id,
        "environment_id": environment_id,
        "sha256": hashlib.sha256(blob).hexdigest(),
        "size_bytes": len(blob),
        "created_at": now_iso(),
        "source_commit": commit,
        "_blob": blob,
    }
    # 产物记录也要过契约：source_commit 必须是完整 40 位 SHA、environment_id 的适用范围
    # 由存储域决定。这层自检拦住的是「产出违约却看起来正常」——那种问题只有下游拿到
    # 记录时才会暴露，而那时已经离出错点很远了。
    problems = schema_errors(
        "artifact", {k: v for k, v in record.items() if not k.startswith("_")})
    if problems:
        # 产出违约归 EXEC_4003：它发生在执行期，但码本身已区别于「超时」，
        # 调度器不会误判成可重试。此前检测类借用 ANALYSIS_5001、其余借用 EXEC_4002。
        raise JobAbort("EXEC_4003", "EXEC",
                       "mock 登记的产物记录不符合 artifact 契约。", "; ".join(problems))
    ARTIFACTS[art_id] = record
    return art_id


def register_json_artifact(job: dict, artifact_type: str,
                           filename: str, content: dict) -> str:
    """把当前检测 job 的 JSON 产物登记到下载接口。

    走的是同一个 register_artifact：产物记录的构造、存储域映射与契约自检只有一份，
    检测侧不会因为「另写一份」而漏掉后来的约束。
    """
    blob = (json.dumps(content, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return register_artifact(
        job["job_id"], job["job_type"], job["input"]["repository"]["commit"],
        blob, "application/json", artifact_type=artifact_type, filename=filename,
        environment_id=job["input"]["environment_id"])


def load_static_records() -> None:
    """登记可下载的契约样例与环境记录，并拒绝摘要不一致的启动。"""
    for record_name, content_path in STATIC_ARTIFACTS:
        record = json.loads((SAMPLES / record_name).read_text(encoding="utf-8"))
        blob = content_path.read_bytes()
        if hashlib.sha256(blob).hexdigest() != record.get("sha256"):
            raise RuntimeError(f"样例摘要失效：{record_name}")
        if len(blob) != record.get("size_bytes"):
            raise RuntimeError(f"样例大小失效：{record_name}")
        ARTIFACTS[record["artifact_id"]] = {**record, "_blob": blob}

    for sample_name in STATIC_ENVIRONMENTS:
        env = json.loads((SAMPLES / sample_name).read_text(encoding="utf-8"))
        ENVIRONMENTS[env["environment_id"]] = env


# -------------------------------------------------------------- 任务生命周期

def output_for_draft(job: dict) -> dict:
    """环境生成：产出 Dockerfile 与镜像产物，并登记一个可查询的环境。"""
    payload = job["input"]
    commit = resolve_commit(payload["repository"].get("commit"),
                            payload["repository"].get("url", ""))
    if commit is None:
        # 兜底：这个分支在受理阶段（cross_checks）就该被拦下。留在这里是为了万一
        # 走到这一步也不产出带假提交的记录；载体与受理阶段同码——REQ_1001「请求不合法」。
        raise JobAbort(
            "REQ_1001", "REQ",
            "无法解析仓库版本：输入给的 commit 不是仓库中真实存在的提交。",
            f"input.repository.commit={payload['repository'].get('commit')!r}；"
            f"mock 只能解析本仓库（{SELF_REPO_URL}）的提交。",
        )
    build = payload["build"]
    raw_subdir = build.get("project_subdir") or "."
    segments = raw_subdir.rstrip("/").split("/")
    # 判据看**原始值**：先 strip 再判 startswith("/") 的话，绝对路径会被静默改成相对路径
    # （"/etc" → "etc"），守卫就成了死代码。空段一律拒绝、不做静默折叠——判据要覆盖尾部
    # （"a//"）与中间（"a//b"）两种，只看 split 的结果会把尾部空段吃掉。
    if raw_subdir.startswith("/") or ".." in segments or "" in segments or "//" in raw_subdir:
        raise JobAbort(
            "REQ_1001", "REQ",
            "项目根越界：project_subdir 必须是仓库内的相对路径。",
            f"project_subdir={build.get('project_subdir')!r}；不接受绝对路径、`..` 或空路径段"
            f"——它会被拼成容器内的项目根，越界后构建与验证会作用到别的目录上。",
        )
    parts = [seg for seg in segments if seg != "."]
    workdir = "/workspace" + ("/" + "/".join(parts) if parts else "")

    raw = FIXTURE_DOCKERFILE.read_bytes() if FIXTURE_DOCKERFILE.exists() else b""
    dockerfile_id = register_artifact(
        job["job_id"], "DRAFT", commit, raw, "text/x-dockerfile",
        artifact_type="DOCKERFILE", filename="Dockerfile", environment_id=None)

    image_id = register_artifact(
        job["job_id"], "DRAFT", commit, f"draft-{job['job_id']}:1.0\n".encode("utf-8"),
        "text/plain", artifact_type="IMAGE_REF", filename="image-ref.txt",
        environment_id=None)

    # 环境创建后不可变：改动环境必须新建一个 environment_id。
    # 本函数由 run_job 在持有 LOCK 时调用，这里不再重复加锁。
    env_id = "env-" + job["job_id"][len("job-"):]
    ENVIRONMENTS[env_id] = {
        "environment_id": env_id,
        "image": image_id,
        "project_root": workdir,
        "working_directory": workdir,
        "build_command": build["command"],
        # 能力需求来自调用方：它知道后续要在这个环境里做什么（检测要读文件访问记录）。
        # 环境生成服务只负责把它固定下来，不替调用方猜。
        "runtime_capabilities": list(payload.get("runtime_capabilities") or []),
    }

    job["output"] = {
        "environment_id": env_id,
        "dockerfile_artifact_id": dockerfile_id,
        "image_artifact_id": image_id,
        "resolved_commit": commit,
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
            "verify_stdout": "hello E3\n",
            "rounds_used": 0,
        },
    }


def output_for_repair(job: dict) -> dict:
    payload = job["input"]
    # 受理阶段只在请求声明了 report.commit 时比对；没声明时，要到「取回报告」这一步
    # 才知道报告属于哪个版本——这正是 REPAIR_6001 的意义：不拿失效的报告去生成补丁。
    record = ARTIFACTS.get(payload["report"]["artifact_id"], {})
    doc, _ = report_body(record)
    # 比对的是**报告正文声明的**版本：产物记录的 source_commit 只是元数据，元数据过期
    # 或记错时版本检查就是空转，补丁照样按错误的版本生成出来。
    report_commit = (doc or {}).get("repository", {}).get("commit")
    if report_commit and report_commit != payload["repository"]["commit"]:
        raise JobAbort(
            "REPAIR_6001", "REPAIR",
            "修复输入不可用：报告所依据的源码版本与请求的 repository.commit 不一致，报告失效。",
            f"请求 commit {payload['repository']['commit'][:8]}…，"
            f"报告所依据的 commit {report_commit[:8]}…；请求未声明 report.commit，"
            f"故在执行阶段取回报告后才发现。",
        )
    patch = (
        "--- a/Makefile\n"
        "+++ b/Makefile\n"
        "@@ -5,7 +5,7 @@\n"
        " main.o: main.c\n"
        "-\t$(CC) $(CFLAGS) -c main.c -o main.o\n"
        "+\t$(CC) $(CFLAGS) -c main.c -o main.o\n"
        "+main.o: config.h feature.h\n"
    ).encode()
    art_id = register_artifact(
        job["job_id"], "REPAIR", job["input"]["repository"].get("commit", ""),
        patch, "text/x-diff", artifact_type="PATCH", filename="fix.patch",
        environment_id=job["input"]["environment_id"])
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
        "environment_id": payload["environment_id"],
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
        "environment_id": payload["environment_id"],
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
        "environment_id": payload["environment_id"],
        "actual_graph_artifact_id": actual_id,
        "declared_graph_artifact_id": declared_id,
        "error_report_artifact_id": report_id,
        "summary": {"missing": 0, "redundant": 0},
    }


def output_for_incremental_check(job: dict) -> None:
    report_id = register_json_artifact(
        job, "ERROR_REPORT", "error-report.json",
        report_for(job, "INCREMENTAL", "EChecker-MOCK"))
    payload = job["input"]
    job["output"] = {
        "base_commit": payload["base_commit"],
        "resolved_commit": payload["repository"]["commit"],
        "environment_id": payload["environment_id"],
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
        job["execution"]["attempt"] = 1

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
        try:
            builder(job)
        except JobAbort as abort:
            job.pop("output", None)
            job["status"] = "FAILED"
            job["error"] = {
                "code": abort.code,
                "stage": abort.stage,
                "message": abort.message,
                "detail": abort.detail,
                "at": now_iso(),
            }
            return

        output_errors = schema_errors(OUTPUT_SCHEMA[job["job_type"]], job["output"])
        if output_errors:
            job.pop("output", None)
            job["status"] = "FAILED"
            job["error"] = {
                "code": "EXEC_4003",
                "stage": "EXEC",
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
            self._error(404, "REQ_1002", f"未知端点：POST {self.path}")
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError) as exc:
            self._error(400, "REQ_1001", f"请求体不是合法 JSON：{exc}")
            return

        if isinstance(body, dict) and body.get("job_type") not in (None, job_type):
            self._error(400, "REQ_1001",
                        f"job_type {body.get('job_type')!r} 与端点 {self.path} "
                        f"（{job_type}）不匹配")
            return

        problems = validate_request(body)
        if problems:
            self._error(400, "REQ_1001", problems)
            return

        job_id = new_job_id()
        repo_url = body["input"].get("repository", {}).get("url", "")
        should_fail = repo_url.rstrip("/").endswith("fail")
        should_analysis_fail = (
            job_type in {"FULL_CHECK", "INCREMENTAL_CHECK"}
            and repo_url.rstrip("/").endswith("fail-analysis")
        )

        job = {
            "schema_version": JOB_SCHEMA_VERSION[job_type],
            "job_id": job_id,
            "trace_id": body.get("trace_id", f"trace-{uuid.uuid4().hex[:8]}"),
            "job_type": job_type,
            "status": "QUEUED",
            "execution": {
                "created_at": now_iso(),
                "started_at": None,
                "finished_at": None,
                "attempt": 0,
            },
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
                self._error(404, "REQ_1002", f"任务不存在：{m.group(1)}")
                return
            self._send(200, snapshot)
            return

        m = re.fullmatch(r"/v1/environments/([A-Za-z0-9_-]+)", self.path)
        if m:
            with LOCK:
                env = ENVIRONMENTS.get(m.group(1))
                snapshot = dict(env) if env else None
            if snapshot is None:
                self._error(404, "REQ_1002", f"环境不存在：{m.group(1)}")
                return
            self._send(200, snapshot)
            return

        m = re.fullmatch(r"/v1/artifacts/([A-Za-z0-9_-]+)", self.path)
        if m:
            with LOCK:
                art = ARTIFACTS.get(m.group(1))
                snapshot = dict(art) if art else None
            if snapshot is None:
                self._error(404, "REQ_1002", f"产物不存在：{m.group(1)}")
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

        self._error(404, "REQ_1002", f"未知端点：GET {self.path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="四类任务服务的最小模拟实现")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    load_static_records()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("DevOps mock 已启动（内存态，无持久化）")
    for path, jt in sorted(ENDPOINTS.items()):
        print(f"  POST http://{args.host}:{args.port}{path:<30} {jt}")
    print(f"  GET  http://{args.host}:{args.port}/v1/jobs/{{job_id}}")
    print(f"  GET  http://{args.host}:{args.port}/v1/artifacts/{{artifact_id}}")
    print(f"  GET  http://{args.host}:{args.port}/v1/environments/{{environment_id}}")
    print("  Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
