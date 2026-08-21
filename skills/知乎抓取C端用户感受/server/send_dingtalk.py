#!/usr/bin/env python3
"""Send a complete Zhihu report as chunked Markdown through DingTalk Webhook."""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo


ZONE = ZoneInfo("America/New_York")
REPORTS = Path.home() / "reports/zhihu-customer-voice"
STATE = Path.home() / ".local/state/zhihu-customer-voice/dingtalk-webhook"
DWS = Path.home() / ".npm-global/bin/dws"
DEFAULT_CHUNK_CHARS = 3500
DEFAULT_SEND_INTERVAL_SECONDS = 4.0
MAX_PARTS = 30
REQUIRED_MARKERS = (
    "# ",
    "## 关键词索引",
    "## 枕芯",
    "## 被芯",
    "## 四件套",
    "| 回答 | 作者 | 赞同 | 评论 | 主题 | 短摘录 |",
    "## 运行汇总",
    "## 数据质量说明",
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Send one complete Zhihu report as DingTalk Markdown messages."
    )
    parser.add_argument("--date", help="America/New_York report date in YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Validate and plan only")
    return parser.parse_args()


def required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is missing")
    return value


def positive_number_env(name, default, cast):
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = cast(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} is invalid") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def run_dws(arguments, profile, timeout=90):
    command = [
        str(DWS),
        *arguments,
        "--profile",
        profile,
        "--format",
        "json",
    ]
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    raw = (result.stdout or result.stderr).strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"dws returned non-JSON output (exit={result.returncode})") from exc
    if result.returncode or payload.get("success") is not True:
        message = payload.get("errorMsg") or payload.get("message") or "unknown dws error"
        raise RuntimeError(f"dws failed (exit={result.returncode}): {message}")
    webhook_result = payload.get("result") or {}
    error_code = webhook_result.get("errcode", webhook_result.get("errCode", 0))
    if str(error_code) not in {"0", "None", ""}:
        message = webhook_result.get("errmsg") or webhook_result.get("errMsg") or "webhook error"
        raise RuntimeError(f"DingTalk Webhook failed: {message}")
    return webhook_result


def report_for(report_date):
    path = REPORTS / f"{report_date:%m%d}家纺c端客户抓取.md"
    if not path.is_file() or path.stat().st_size <= 500:
        raise RuntimeError(f"report missing or incomplete: {path}")
    text = path.read_text(encoding="utf-8")
    missing = [marker for marker in REQUIRED_MARKERS if marker not in text]
    if missing:
        raise RuntimeError(f"report failed structure check: {path}; missing {missing}")
    for keyword in ("枕芯", "被芯", "四件套"):
        start = text.find(f"## {keyword}")
        end = text.find("\n## ", start + 1)
        section = text[start : end if end >= 0 else None]
        pattern = r"^### \d+\. \[.+\]\(https://www\.zhihu\.com/question/\d+\)$"
        if not re.search(pattern, section, re.MULTILINE):
            raise RuntimeError(f"report failed question-link check: {keyword}")
    return path, text


def split_long_block(block, max_chars):
    pieces = []
    current = []
    current_length = 0
    for line in block.splitlines():
        line_length = len(line) + (1 if current else 0)
        if current and current_length + line_length > max_chars:
            pieces.append("\n".join(current))
            current = []
            current_length = 0
        if len(line) > max_chars:
            if current:
                pieces.append("\n".join(current))
                current = []
                current_length = 0
            pieces.extend(
                line[index : index + max_chars]
                for index in range(0, len(line), max_chars)
            )
            continue
        current.append(line)
        current_length += line_length
    if current:
        pieces.append("\n".join(current))
    return pieces


def split_markdown(text, max_chars):
    if max_chars < 500:
        raise RuntimeError("DINGTALK_WEBHOOK_CHUNK_CHARS must be at least 500")
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    parts = []
    current = []
    current_length = 0
    for block in blocks:
        candidates = [block] if len(block) <= max_chars else split_long_block(block, max_chars)
        for candidate in candidates:
            addition = len(candidate) + (2 if current else 0)
            if current and current_length + addition > max_chars:
                parts.append("\n\n".join(current))
                current = []
                current_length = 0
            current.append(candidate)
            current_length += len(candidate) + (2 if len(current) > 1 else 0)
    if current:
        parts.append("\n\n".join(current))
    if not parts:
        raise RuntimeError("report produced no Markdown parts")
    if len(parts) > MAX_PARTS:
        raise RuntimeError(f"report needs {len(parts)} parts; maximum is {MAX_PARTS}")
    return parts


def decorated_part(content, report_date, part_number, total_parts):
    heading = (
        f"### 家纺报告｜{report_date:%Y-%m-%d}"
        f"｜第 {part_number}/{total_parts} 部分"
    )
    return f"{heading}\n\n{content}"


def atomic_json(path, payload):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def main():
    args = parse_args()
    report_date = (
        dt.date.fromisoformat(args.date)
        if args.date
        else dt.datetime.now(ZONE).date()
    )
    path, report_text = report_for(report_date)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    STATE.mkdir(parents=True, exist_ok=True)
    marker_base = f"{report_date.isoformat()}-{digest[:16]}"
    complete_marker = STATE / f"{marker_base}.sent"
    if complete_marker.exists():
        print(f"already sent as DingTalk Markdown: {path.name}")
        return 0

    profile = required_env("DINGTALK_PROFILE")
    token = required_env("DINGTALK_WEBHOOK_TOKEN")
    chunk_chars = positive_number_env(
        "DINGTALK_WEBHOOK_CHUNK_CHARS", DEFAULT_CHUNK_CHARS, int
    )
    if chunk_chars < 600:
        raise RuntimeError("DINGTALK_WEBHOOK_CHUNK_CHARS must be at least 600")
    interval = positive_number_env(
        "DINGTALK_WEBHOOK_INTERVAL_SECONDS",
        DEFAULT_SEND_INTERVAL_SECONDS,
        float,
    )
    if not DWS.is_file():
        raise RuntimeError(f"dws executable not found: {DWS}")

    parts = split_markdown(report_text, chunk_chars - 100)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "action": "send_dingtalk_webhook_markdown",
                    "file": str(path),
                    "size": path.stat().st_size,
                    "parts": len(parts),
                    "part_chars": [
                        len(decorated_part(part, report_date, index, len(parts)))
                        for index, part in enumerate(parts, start=1)
                    ],
                    "profile_configured": bool(profile),
                    "webhook_configured": bool(token),
                    "resume_supported": True,
                },
                ensure_ascii=False,
            )
        )
        return 0

    sent_parts = 0
    for index, part in enumerate(parts, start=1):
        part_marker = STATE / f"{marker_base}.part-{index:02d}-of-{len(parts):02d}.sent"
        if part_marker.exists():
            sent_parts += 1
            continue
        title = f"家纺报告 {report_date:%m%d}（{index}/{len(parts)}）"
        text = decorated_part(part, report_date, index, len(parts))
        run_dws(
            [
                "chat",
                "message",
                "send-by-webhook",
                "--token",
                token,
                "--title",
                title,
                "--text",
                text,
            ],
            profile,
        )
        atomic_json(
            part_marker,
            {
                "file": str(path),
                "sha256": digest,
                "part": index,
                "parts": len(parts),
                "status": "SUCCESS",
                "sentAt": dt.datetime.now(ZONE).isoformat(),
            },
        )
        sent_parts += 1
        if index < len(parts):
            time.sleep(interval)

    if sent_parts != len(parts):
        raise RuntimeError(f"only {sent_parts}/{len(parts)} Markdown parts were sent")
    atomic_json(
        complete_marker,
        {
            "file": str(path),
            "sha256": digest,
            "parts": len(parts),
            "mode": "dingtalk_webhook_markdown",
            "sendStatus": "SUCCESS",
            "completedAt": dt.datetime.now(ZONE).isoformat(),
        },
    )
    print(f"sent to DingTalk Webhook as Markdown: {path.name}; parts={len(parts)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
