"""Background subprocess jobs for long-running Streamlit UI actions."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_MAX_OUTPUT_LINES = 300
_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.RLock()


def _job_runtime() -> tuple[dict[str, dict[str, Any]], threading.RLock]:
    return _JOBS, _LOCK


def _job_key(campaign_id: str, stage: str, volume_index: int) -> str:
    return f"{campaign_id}:{stage}:vol{volume_index:02d}"


def _pipeline_env(campaign_id: str) -> dict[str, str]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(_ROOT / "src")}
    env["DEFAULT_CAMPAIGN_ID"] = campaign_id
    return env


def _run_job(key: str, cmd: list[str], campaign_id: str) -> None:
    jobs, lock = _job_runtime()
    try:
        with subprocess.Popen(
            cmd,
            cwd=str(_ROOT),
            env=_pipeline_env(campaign_id),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        ) as proc:
            with lock:
                job = jobs.get(key)
                if job is not None:
                    job["pid"] = proc.pid
            if proc.stdout is not None:
                for line in proc.stdout:
                    with lock:
                        job = jobs.get(key)
                        if job is None:
                            continue
                        job["output"].append(line)
                        if len(job["output"]) > _MAX_OUTPUT_LINES:
                            job["output"] = job["output"][-_MAX_OUTPUT_LINES:]
                        job["updated_at"] = time.time()
            returncode = proc.wait()
            with lock:
                job = jobs.get(key)
                if job is not None:
                    job["returncode"] = returncode
                    job["status"] = "succeeded" if returncode == 0 else "failed"
                    job["finished_at"] = time.time()
                    job["updated_at"] = time.time()
    except Exception as exc:
        with lock:
            job = jobs.get(key)
            if job is not None:
                job["status"] = "failed"
                job["returncode"] = -1
                job["error"] = str(exc)
                job["output"].append(f"[UI ERROR] {exc}\n")
                job["finished_at"] = time.time()
                job["updated_at"] = time.time()


def start_pipeline_job(
    *,
    campaign_id: str,
    stage: str,
    volume_index: int,
    args: list[str],
    label: str,
) -> tuple[dict[str, Any], bool]:
    """Start a background pipeline command, or return the running duplicate."""
    jobs, lock = _job_runtime()
    key = _job_key(campaign_id, stage, volume_index)
    cmd = [sys.executable, "-m", "trpg2novel.pipeline"] + args
    with lock:
        current = jobs.get(key)
        if current and current.get("status") == "running":
            return dict(current), False
        now = time.time()
        job = {
            "id": uuid.uuid4().hex,
            "key": key,
            "campaign_id": campaign_id,
            "stage": stage,
            "volume_index": volume_index,
            "label": label,
            "args": list(args),
            "cmd": cmd,
            "status": "running",
            "pid": None,
            "returncode": None,
            "output": ["$ " + " ".join(args) + "\n"],
            "error": "",
            "started_at": now,
            "updated_at": now,
            "finished_at": None,
        }
        jobs[key] = job
    thread = threading.Thread(target=_run_job, args=(key, cmd, campaign_id), daemon=True)
    thread.start()
    return snapshot_job(campaign_id=campaign_id, stage=stage, volume_index=volume_index) or job, True


def snapshot_job(*, campaign_id: str, stage: str, volume_index: int) -> dict[str, Any] | None:
    jobs, lock = _job_runtime()
    key = _job_key(campaign_id, stage, volume_index)
    with lock:
        job = jobs.get(key)
        if job is None:
            return None
        snap = dict(job)
        snap["output"] = list(job.get("output") or [])
        return snap


def job_elapsed(job: dict[str, Any]) -> str:
    end = job.get("finished_at") or time.time()
    seconds = max(0, int(end - float(job.get("started_at") or end)))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def chapterize_progress(job: dict[str, Any], total_estimate: int | None) -> tuple[int, int | None]:
    output = "".join(job.get("output") or [])
    matches = re.findall(r"第\s*(\d+)\s*章切分完成", output)
    done = len(matches)
    total = int(total_estimate or 0) or None
    if total is not None:
        total = max(done, total)
    return done, total


def output_tail(job: dict[str, Any], lines: int = 80) -> str:
    return "".join((job.get("output") or [])[-lines:])
