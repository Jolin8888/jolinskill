#!/usr/bin/env python3
import datetime as dt
import fcntl
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ZH = Path.home() / ".local/bin/zhihu"
OUT = Path.home() / "reports/zhihu-customer-voice"
LOG = Path.home() / ".local/state/zhihu-customer-voice"
KEYWORDS = ("枕芯", "被芯", "四件套")
ALLOWED_TYPES = {"answer", "article", "question"}


def clean(value, limit=180):
    value = html.unescape(re.sub(r"<[^>]+>", "", str(value or "")))
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def run_search(keyword):
    result = subprocess.run(
        [str(ZH), "search", keyword, "--limit", "30", "--answers", "5", "--json"],
        text=True, capture_output=True, timeout=180, check=False,
    )
    if result.returncode:
        raise RuntimeError(clean(result.stderr or result.stdout, 300))
    return json.loads(result.stdout)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    LOG.mkdir(parents=True, exist_ok=True)
    with (LOG / "run.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another run is active", file=sys.stderr)
            return 75

        now = dt.datetime.now(ZoneInfo("America/New_York"))
        target = OUT / f"{now:%m%d}家纺c端客户抓取.md"
        if target.exists() and target.stat().st_size > 500:
            print(f"already complete: {target}")
            return 0

        sections, total = [], 0
        for keyword in KEYWORDS:
            payload = run_search(keyword)
            rows = []
            for item in payload.get("data", []):
                obj = item.get("object") or {}
                typ = obj.get("type")
                if typ not in ALLOWED_TYPES:
                    continue
                question = obj.get("question") or {}
                title = clean(question.get("title") or obj.get("title") or item.get("highlight", {}).get("title"), 120)
                excerpt = clean(obj.get("excerpt") or obj.get("content") or item.get("highlight", {}).get("description"), 80)
                author = clean((obj.get("author") or {}).get("name") or "未知", 60)
                url = obj.get("url") or question.get("url") or ""
                if typ == "answer" and obj.get("id") and question.get("id"):
                    url = f"https://www.zhihu.com/question/{question['id']}/answer/{obj['id']}"
                elif typ == "article" and obj.get("id"):
                    url = f"https://zhuanlan.zhihu.com/p/{obj['id']}"
                elif typ == "question" and obj.get("id"):
                    url = f"https://www.zhihu.com/question/{obj['id']}"
                else:
                    url = url.replace("https://api.zhihu.com/questions/", "https://www.zhihu.com/question/")
                rows.append((typ, title or "无标题", author, excerpt or "无可见摘要", url))
                if len(rows) >= 10:
                    break
            total += len(rows)
            body = [f"## {keyword}", "", "| 类型 | 标题 | 作者 | 短摘录 | 链接 |", "|---|---|---|---|---|"]
            body += [f"| {t} | {a.replace('|','／')} | {b.replace('|','／')} | {c.replace('|','／')} | {u} |" for t,a,b,c,u in rows]
            if not rows:
                body.append("| - | 无有效问题、回答或文章 | - | - | - |")
            sections.append("\n".join(body))

        if total == 0:
            raise RuntimeError("three searches returned no valid content")
        report = (
            f"# {now:%m%d} 家纺 C 端用户感受\n\n"
            f"- 运行时间：{now:%Y-%m-%d %H:%M:%S} America/New_York\n"
            f"- 运行位置：007 服务器\n- 关键词：{'、'.join(KEYWORDS)}\n"
            f"- 有效条目：{total}\n- 数据说明：仅保留可追溯的问题、回答和文章；不收录用户、广告或未知类型。\n\n"
            + "\n\n".join(sections) + "\n"
        )
        temp = target.with_suffix(".tmp")
        temp.write_text(report, encoding="utf-8")
        os.replace(temp, target)
        print(f"created: {target} ({total} items)")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAILED: {clean(exc, 500)}", file=sys.stderr)
        raise SystemExit(1)
