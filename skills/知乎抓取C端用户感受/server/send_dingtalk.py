#!/usr/bin/env python3
"""Publish one complete Zhihu report as a DingTalk document and send its link."""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo


ZONE = ZoneInfo("America/New_York")
REPORTS = Path.home() / "reports/zhihu-customer-voice"
STATE = Path.home() / ".local/state/zhihu-customer-voice/dingtalk-document-n8n"
DWS = Path.home() / ".npm-global/bin/dws"
ENTRY_LABEL = "点击查看完整市场情报"
REQUIRED_MARKERS = (
    "# 国内家纺C端客户需求汇总",
    "## 一、今日核心判断",
    "## 二、三类产品需求观察",
    "## 三、消费者共同关注点",
    "## 四、建议优先动作",
    "## 五、原始问题与回答明细",
    "### 枕芯原始明细",
    "### 被芯原始明细",
    "### 四件套原始明细",
    "| 回答链接 | 作者 | 赞同数 | 评论数 | 可见短摘录 |",
    "## 六、采集概况与数据说明",
)
READBACK_MARKERS = (
    "今日核心判断",
    "三类产品需求观察",
    "消费者共同关注点",
    "建议优先动作",
    "原始问题与回答明细",
    "采集概况与数据说明",
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Create a group-readable DingTalk report document and send one link."
    )
    parser.add_argument("--date", help="America/New_York report date in YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Validate and plan only")
    return parser.parse_args()


def required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is missing")
    return value


def run_dws(arguments, profile, timeout=180):
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
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        try:
            payload = json.loads(raw[start : end + 1])
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(
                f"dws returned non-JSON output (exit={result.returncode})"
            ) from exc
    success_flags = [
        child
        for key, child in walk_values(payload)
        if key.lower() == "success" and isinstance(child, bool)
    ]
    error_message = first_string(payload, ("errorMsg", "error_message"))
    structured_error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(structured_error, dict) and structured_error:
        error_message = error_message or first_string(
            structured_error, ("message", "hint", "reason")
        )
    failed = (
        result.returncode != 0
        or any(flag is False for flag in success_flags)
        or bool(structured_error)
    )
    if failed or error_message:
        message = error_message or first_string(payload, ("message",)) or "unknown dws error"
        raise RuntimeError(f"dws failed (exit={result.returncode}): {message}")
    dws_result = payload.get("result", payload)
    if isinstance(dws_result, dict):
        error_code = dws_result.get("errcode", dws_result.get("errCode", 0))
        if str(error_code) not in {"0", "None", ""}:
            message = dws_result.get("errmsg") or dws_result.get("errMsg") or "DingTalk error"
            raise RuntimeError(f"DingTalk failed: {message}")
    return dws_result


def report_for(report_date):
    path = REPORTS / f"{report_date:%m%d}家纺c端客户抓取.md"
    if not path.is_file() or path.stat().st_size <= 500:
        raise RuntimeError(f"report missing or incomplete: {path}")
    text = path.read_text(encoding="utf-8")
    missing = [marker for marker in REQUIRED_MARKERS if marker not in text]
    if missing:
        raise RuntimeError(f"report failed structure check: {path}; missing {missing}")
    for keyword in ("枕芯", "被芯", "四件套"):
        start = text.find(f"### {keyword}原始明细")
        end = text.find("\n### ", start + 1)
        section = text[start : end if end >= 0 else None]
        pattern = r"^#### \d+\. \[.+\]\(https://www\.zhihu\.com/question/\d+\)$"
        if not re.search(pattern, section, re.MULTILINE):
            raise RuntimeError(f"report failed question-link check: {keyword}")
    return path, text


def atomic_json(path, payload):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid state file: {path}") from exc


def walk_values(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_values(child)


def walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def first_string(value, keys):
    wanted = {key.lower() for key in keys}
    for key, child in walk_values(value):
        if key.lower() in wanted and isinstance(child, str) and child.strip():
            return child.strip()
    return ""


def document_content(value):
    if isinstance(value, str):
        return value
    content = first_string(value, ("content", "markdown", "text", "body"))
    if content:
        return content
    return json.dumps(value, ensure_ascii=False)


def validate_readback(source, returned):
    missing = [marker for marker in READBACK_MARKERS if marker not in returned]
    if missing:
        raise RuntimeError(f"DingTalk document readback is incomplete; missing {missing}")
    source_questions = set(re.findall(r"zhihu\.com/question/(\d+)", source))
    returned_questions = set(re.findall(r"zhihu\.com/question/(\d+)", returned))
    if not source_questions or not source_questions.issubset(returned_questions):
        raise RuntimeError("DingTalk document readback lost question links")


def document_node(created):
    node = first_string(
        created,
        ("nodeId", "node_id", "dentryUuid", "dentry_uuid", "uuid"),
    )
    if not node:
        raise RuntimeError("dws doc create returned no document node ID")
    return node


def exact_document(search_result, name):
    matches = []
    seen = set()
    for _, child in walk_values(search_result):
        if not isinstance(child, list):
            continue
        for item in child:
            if not isinstance(item, dict) or item.get("name") != name:
                continue
            node = first_string(
                item,
                ("nodeId", "node_id", "fileId", "dentryUuid", "uuid"),
            )
            if node and node not in seen:
                seen.add(node)
                matches.append((node, item))
    if len(matches) > 1:
        raise RuntimeError(f"multiple DingTalk documents match {name}")
    return matches[0] if matches else ("", {})


def document_url(value, node):
    for key, child in walk_values(value):
        if key.lower() in {
            "publishurl",
            "publishedurl",
            "shareurl",
            "openurl",
            "url",
        } and isinstance(child, str):
            if child.startswith("https://alidocs.dingtalk.com/"):
                return child
    return f"https://alidocs.dingtalk.com/i/nodes/{node}"


def group_member_user_ids(profile, group_id):
    members = []
    cursor = "0"
    for _ in range(20):
        page = run_dws(
            ["chat", "group", "members", "--id", group_id, "--cursor", cursor],
            profile,
        )
        if not isinstance(page, dict):
            raise RuntimeError("DingTalk group members returned an invalid result")
        members.extend(page.get("list") or [])
        if not page.get("hasMore"):
            break
        next_cursor = page.get("nextCursor") or page.get("cursor")
        if not next_cursor or str(next_cursor) == cursor:
            raise RuntimeError("DingTalk group member pagination has no next cursor")
        cursor = str(next_cursor)
    else:
        raise RuntimeError("DingTalk group member pagination exceeded 20 pages")

    user_ids = []
    for member in members:
        open_id = first_string(member, ("openDingtalkId", "openDingTalkId"))
        name = first_string(
            member,
            ("memberEmpName", "memberNick", "memberGroupNick"),
        )
        if not open_id or not name:
            raise RuntimeError("DingTalk group member has no resolvable identity")
        searched = run_dws(
            ["contact", "user", "search", "--query", name],
            profile,
        )
        matches = set()
        for candidate in walk_dicts(searched):
            candidate_open_id = first_string(
                candidate, ("openDingtalkId", "openDingTalkId")
            )
            candidate_user_id = first_string(
                candidate, ("userId", "userid", "staffId")
            )
            if candidate_open_id == open_id and candidate_user_id:
                matches.add(candidate_user_id)
        if len(matches) != 1:
            raise RuntimeError(f"DingTalk group member could not be uniquely resolved: {name}")
        user_ids.append(matches.pop())
    return sorted(set(user_ids))


def permission_user_ids(value):
    permitted = set()
    accepted_roles = {"OWNER", "MANAGER", "EDITOR", "DOWNLOADER", "READER"}
    for item in walk_dicts(value):
        user_id = first_string(item, ("id", "userId", "userid", "staffId"))
        role = first_string(item, ("role", "permissionRole", "roleType")).upper()
        if user_id and role in accepted_roles:
            permitted.add(user_id)
    return permitted


def grant_group_read_access(node, profile, group_id):
    target_users = set(group_member_user_ids(profile, group_id))
    current_user = first_string(
        run_dws(["contact", "user", "get-self"], profile),
        ("userId", "userid", "staffId"),
    )
    if not current_user:
        raise RuntimeError("DingTalk current user ID could not be resolved")
    target_users.discard(current_user)

    existing = permission_user_ids(
        run_dws(
            ["drive", "permission", "list", "--node", node, "--limit", "50"],
            profile,
        )
    )
    missing = sorted(target_users - existing)
    for start in range(0, len(missing), 30):
        batch = missing[start : start + 30]
        run_dws(
            [
                "drive",
                "permission",
                "add",
                "--node",
                node,
                "--users",
                ",".join(batch),
                "--role",
                "READER",
            ],
            profile,
        )
    verified = permission_user_ids(
        run_dws(
            ["drive", "permission", "list", "--node", node, "--limit", "50"],
            profile,
        )
    )
    if not target_users.issubset(verified):
        raise RuntimeError("DingTalk group document READER access was not verified")
    return len(target_users) + 1


def validate_n8n_webhook_url(value):
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("N8N_DINGTALK_WEBHOOK_URL must use local HTTP loopback")
    if parsed.port not in {None, 5678} or not parsed.path.startswith("/webhook/"):
        raise RuntimeError("N8N_DINGTALK_WEBHOOK_URL has an invalid port or path")
    return value


def deliver_through_n8n(url, payload, timeout=45):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"n8n delivery failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"n8n delivery request failed: {exc.reason}") from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("n8n delivery returned non-JSON output") from exc
    if result.get("ok") is not True:
        message = result.get("errmsg") or result.get("message") or "unknown n8n error"
        raise RuntimeError(f"n8n/DingTalk delivery was not successful: {message}")
    if result.get("idempotency_key") != payload["idempotency_key"]:
        raise RuntimeError("n8n delivery response idempotency key mismatch")
    return result


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
    document_marker = STATE / f"{marker_base}.document.json"
    access_marker = STATE / f"{marker_base}.access.json"
    notification_marker = STATE / f"{marker_base}.notification.json"
    complete_marker = STATE / f"{marker_base}.complete.json"
    if complete_marker.exists():
        print(f"already published as a DingTalk document: {path.name}")
        return 0

    profile = required_env("DINGTALK_PROFILE")
    group_id = required_env("DINGTALK_GROUP_ID")
    n8n_url = validate_n8n_webhook_url(required_env("N8N_DINGTALK_WEBHOOK_URL"))
    if not DWS.is_file():
        raise RuntimeError(f"dws executable not found: {DWS}")

    document_name = f"家纺C端市场情报 {report_date:%Y-%m-%d}"
    if args.dry_run:
        print(
            json.dumps(
                {
                    "action": "publish_dingtalk_document_and_send_link",
                    "file": str(path),
                    "size": path.stat().st_size,
                    "document_name": document_name,
                    "document_access": "CURRENT_GROUP_MEMBERS_READER",
                    "group_message": ENTRY_LABEL,
                    "profile_configured": bool(profile),
                    "n8n_webhook_configured": bool(n8n_url),
                    "resume_supported": True,
                },
                ensure_ascii=False,
            )
        )
        return 0

    if document_marker.exists():
        document_state = read_json(document_marker)
        if document_state.get("sha256") != digest:
            raise RuntimeError("document state digest does not match report")
        node = str(document_state.get("nodeId") or "")
        if not node:
            raise RuntimeError("document state has no nodeId")
    else:
        searched = run_dws(
            ["drive", "search", "--query", document_name, "--limit", "20"],
            profile,
        )
        node, created = exact_document(searched, document_name)
        recovered = bool(node)
        if not node:
            created = run_dws(
                [
                    "doc",
                    "create",
                    "--name",
                    document_name,
                    "--content-file",
                    str(path),
                ],
                profile,
            )
            node = document_node(created)
        document_state = {
            "file": str(path),
            "sha256": digest,
            "nodeId": node,
            "url": document_url(created, node),
            "recovered": recovered,
            "createdAt": dt.datetime.now(ZONE).isoformat(),
        }
        atomic_json(document_marker, document_state)

    returned = document_content(
        run_dws(["doc", "read", "--node", node], profile)
    )
    validate_readback(report_text, returned)

    if access_marker.exists():
        access_state = read_json(access_marker)
        if access_state.get("nodeId") != node:
            raise RuntimeError("access state does not match document")
    else:
        member_count = grant_group_read_access(node, profile, group_id)
        access_state = {
            "nodeId": node,
            "access": "CURRENT_GROUP_MEMBERS_READER",
            "memberCount": member_count,
            "verifiedAt": dt.datetime.now(ZONE).isoformat(),
        }
        atomic_json(access_marker, access_state)

    link = document_url(document_state, node)

    if not notification_marker.exists():
        idempotency_key = f"zhihu-market-intelligence:{report_date.isoformat()}:{digest[:16]}"
        message_title = (
            "国内家纺C端客户需求汇总｜"
            f"{report_date.year}年{report_date.month:02d}月{report_date.day:02d}日"
        )
        delivery = deliver_through_n8n(
            n8n_url,
            {
                "title": message_title,
                "document_url": link,
                "report_date": report_date.isoformat(),
                "idempotency_key": idempotency_key,
            },
        )
        atomic_json(
            notification_marker,
            {
                "nodeId": node,
                "url": link,
                "title": message_title,
                "message": ENTRY_LABEL,
                "route": "n8n_to_dingtalk_webhook",
                "idempotencyKey": idempotency_key,
                "n8nDuplicate": bool(delivery.get("duplicate")),
                "sentAt": dt.datetime.now(ZONE).isoformat(),
            },
        )

    atomic_json(
        complete_marker,
        {
            "file": str(path),
            "sha256": digest,
            "nodeId": node,
            "url": link,
            "access": "CURRENT_GROUP_MEMBERS_READER",
            "sendStatus": "SUCCESS",
            "completedAt": dt.datetime.now(ZONE).isoformat(),
        },
    )
    print(f"published DingTalk document and delivered one link through n8n: {path.name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
