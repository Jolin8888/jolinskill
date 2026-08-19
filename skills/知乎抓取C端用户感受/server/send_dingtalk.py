#!/usr/bin/env python3
import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo


ZONE = ZoneInfo("America/New_York")
REPORTS = Path.home() / "reports/zhihu-customer-voice"
STATE = Path.home() / ".local/state/zhihu-customer-voice/dingtalk"
DWS = Path.home() / ".npm-global/bin/dws"
REQUIRED_MARKERS = ("# ", "## 枕芯", "## 被芯", "## 四件套")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Send one complete Zhihu customer-voice report to DingTalk."
    )
    parser.add_argument("--date", help="America/New_York report date in YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Validate only; do not send")
    return parser.parse_args()


def required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is missing")
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
    return payload.get("result") or {}


def report_for(report_date):
    path = REPORTS / f"{report_date:%m%d}家纺c端客户抓取.md"
    if not path.is_file() or path.stat().st_size <= 500:
        raise RuntimeError(f"report missing or incomplete: {path}")
    text = path.read_text(encoding="utf-8")
    missing = [marker for marker in REQUIRED_MARKERS if marker not in text]
    if missing:
        raise RuntimeError(f"report failed structure check: {path}; missing {missing}")
    return path


def main():
    args = parse_args()
    report_date = (
        dt.date.fromisoformat(args.date)
        if args.date
        else dt.datetime.now(ZONE).date()
    )
    path = report_for(report_date)
    group_id = required_env("DINGTALK_GROUP_ID")
    group_name = required_env("DINGTALK_GROUP_NAME")
    profile = required_env("DINGTALK_PROFILE")
    if not DWS.is_file():
        raise RuntimeError(f"dws executable not found: {DWS}")

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    STATE.mkdir(parents=True, exist_ok=True)
    marker = STATE / f"{report_date.isoformat()}-{digest[:16]}.sent"
    if marker.exists():
        print(f"already sent to DingTalk: {path.name}")
        return 0

    message_uuid = f"zhihu-customer-voice-{report_date.isoformat()}-{digest[:16]}"
    if args.dry_run:
        search_result = run_dws(
            ["chat", "search", "--query", group_name, "--limit", "20", "--cursor", "0"],
            profile,
        )
        exact_matches = [
            group
            for group in search_result.get("groups", [])
            if group.get("title") == group_name
            and group.get("openConversationId") == group_id
        ]
        if len(exact_matches) != 1:
            raise RuntimeError("configured DingTalk group did not resolve to one exact match")
        print(
            json.dumps(
                {
                    "action": "send_dingtalk_file",
                    "file": str(path),
                    "size": path.stat().st_size,
                    "group_configured": bool(group_id),
                    "group_verified": True,
                    "profile_configured": bool(profile),
                    "uuid": message_uuid,
                },
                ensure_ascii=False,
            )
        )
        return 0

    sent = run_dws(
        [
            "chat",
            "message",
            "send",
            "--group",
            group_id,
            "--msg-type",
            "file",
            "--file-path",
            str(path),
            "--uuid",
            message_uuid,
        ],
        profile,
    )
    task_id = str(sent.get("openTaskId") or "")
    if not task_id:
        raise RuntimeError("dws send returned no openTaskId")

    status_result = {}
    for attempt in range(10):
        status_result = run_dws(
            ["chat", "message", "query-send-status", "--open-task-id", task_id],
            profile,
        )
        status = str(status_result.get("sendStatus") or "").upper()
        if status == "SUCCESS":
            break
        if status in {"FAILED", "FAIL", "ERROR"}:
            raise RuntimeError(f"DingTalk send failed: {status}")
        if attempt < 9:
            time.sleep(2)
    else:
        raise RuntimeError("DingTalk send status did not become SUCCESS")

    marker.write_text(
        json.dumps(
            {
                "file": str(path),
                "sha256": digest,
                "openTaskId": task_id,
                "openMessageId": status_result.get("openMessageId"),
                "sendStatus": status_result.get("sendStatus"),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(marker, 0o600)
    print(f"sent to DingTalk: {path.name}; status=SUCCESS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
