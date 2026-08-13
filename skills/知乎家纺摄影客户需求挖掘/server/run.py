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
ROOT = Path.home() / "reports/zhihu-photo-demand-radar"
STATE = Path.home() / ".local/state/zhihu-photo-demand-radar"
CLUSTERS = (
    ("C04", "色差与实物还原", "家纺 拍摄 色差 退货"),
    ("C13", "摄影公司选择", "家纺 摄影公司 怎么选"),
    ("C17/C25", "返工、验收与翻车", "家纺 摄影 返工 验收"),
    ("C29", "南通/叠石桥本地需求", "南通 叠石桥 家纺 摄影"),
    ("C14", "寄拍/上门", "家纺 寄拍 摄影 上门"),
)
ALLOWED = {"answer", "article", "question"}
PHOTO_TERMS = ("摄影", "拍摄", "拍图", "图片", "寄拍", "影棚", "色差", "主图", "视觉", "验收")


def clean(value, limit=180):
    text = html.unescape(re.sub(r"<[^>]+>", "", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def normalize(text):
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text).lower()


def search(query):
    proc = subprocess.run(
        [str(ZH), "search", query, "--limit", "30", "--answers", "5", "--json"],
        text=True, capture_output=True, timeout=180, check=False,
    )
    if proc.returncode:
        raise RuntimeError(clean(proc.stderr or proc.stdout, 400))
    return json.loads(proc.stdout)


def browser_url(obj, typ):
    question = obj.get("question") or {}
    if typ == "answer" and obj.get("id") and question.get("id"):
        return f"https://www.zhihu.com/question/{question['id']}/answer/{obj['id']}"
    if typ == "article" and obj.get("id"):
        return f"https://zhuanlan.zhihu.com/p/{obj['id']}"
    if typ == "question" and obj.get("id"):
        return f"https://www.zhihu.com/question/{obj['id']}"
    return obj.get("url") or question.get("url") or ""


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "run.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 75
        now = dt.datetime.now(ZoneInfo("America/New_York"))
        stem = f"{now:%m%d}老钱摄影抓取"
        md_path, json_path = ROOT / f"{stem}.md", ROOT / f"{stem}.json"
        if md_path.exists() and json_path.exists() and md_path.stat().st_size > 500:
            json.loads(json_path.read_text(encoding="utf-8"))
            print(f"already complete: {md_path} and {json_path}")
            return 0

        ledger = ROOT / "HISTORY_LEDGER.md"
        cluster_history = ROOT / "INSIGHT_CLUSTER_HISTORY.md"
        old_ledger = ledger.read_text(encoding="utf-8") if ledger.exists() else "# HISTORY_LEDGER\n"
        old_clusters = cluster_history.read_text(encoding="utf-8") if cluster_history.exists() else "# INSIGHT_CLUSTER_HISTORY\n"
        seen_urls = set(re.findall(r"https?://[^\s|]+", old_ledger))
        seen_titles = {normalize(x) for x in re.findall(r"\|\s*([^|]+?)\s*\|", old_ledger) if len(x) > 8}
        radar, new_seen = [], set()
        for cluster, theme, query in CLUSTERS:
            rank = 0
            for item in search(query).get("data", []):
                obj = item.get("object") or {}
                typ = obj.get("type")
                if typ not in ALLOWED:
                    continue
                question = obj.get("question") or {}
                title = clean(question.get("title") or obj.get("title") or item.get("highlight", {}).get("title"), 160)
                url = browser_url(obj, typ)
                excerpt = clean(obj.get("excerpt") or obj.get("content") or item.get("highlight", {}).get("description"), 80)
                if not any(term in title + excerpt for term in PHOTO_TERMS):
                    continue
                if not title or not url or url in seen_urls or url in new_seen or normalize(title) in seen_titles:
                    continue
                votes = int(obj.get("voteup_count") or 0)
                comments = int(obj.get("comment_count") or 0)
                rank += 1
                score = min(100, 55 + min(votes, 20) + min(comments, 10) + (10 if "家纺" in title else 0))
                radar.append({"keyword": query, "rank": rank, "type": typ, "id": str(obj.get("id") or ""),
                    "title": title, "url": url, "author": clean((obj.get("author") or {}).get("name") or "未知", 60),
                    "votes": votes, "comments": comments, "excerpt": excerpt, "cluster": cluster,
                    "theme": theme, "score": score})
                new_seen.add(url)
                if rank >= 5:
                    break
        if not radar:
            raise RuntimeError("no new deduplicated candidates")
        radar.sort(key=lambda x: (-x["score"], x["cluster"], x["rank"]))
        payload = {"run_date": now.strftime("%Y-%m-%d"), "run_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": "America/New_York", "source": {"cli": "zhihu-cli 0.2.4 on 007", "mode": "read-only",
            "quote_limit": "80 Chinese characters"}, "clusters": [x[0] for x in CLUSTERS], "radar": radar,
            "next_keywords": ["家纺摄影报价明细", "床品拍摄验收标准", "叠石桥寄拍", "家纺图片色差退货", "摄影交付延期"]}
        lines = [f"# {stem}", "", "## Opportunity Score 口径", "",
            "基础 55 分；可见赞同、评论与家纺标题相关性加分。仅用于本轮候选排序，不代表知乎官方热度。", "",
            "## 候选需求", "", "| 分数 | Cluster | 痛点/问题 | 作者 | 可见赞同/评论 | 短原话 | 链接 |", "|---:|---|---|---|---:|---|---|"]
        for x in radar:
            vals = [str(x["score"]), x["cluster"], x["title"], x["author"], f'{x["votes"]}/{x["comments"]}', x["excerpt"], x["url"]]
            lines.append("| " + " | ".join(v.replace("|", "／") for v in vals) + " |")
        lines += ["", "## 内容与 SEO 机会", ""] + [f"- {c} {t}：围绕“{q}”制作客户决策、避坑和验收内容。" for c,t,q in CLUSTERS]
        lines += ["", "## 下一轮关键词", ""] + [f"- {x}" for x in payload["next_keywords"]]
        lines += ["", "## 数据质量", "", f"- 新增去重候选：{len(radar)}。", "- 已在候选进入前执行历史 URL 与标题归一化去重。", "- 综合搜索可能包含营销专栏；候选不等同于纯 C 端原话，需人工复核。", "- 单条摘录不超过 80 个汉字；未复制回答全文。", ""]
        ledger_add = "\n".join(f'| {now:%Y-%m-%d} | {x["url"]} | {x["title"].replace("|","／")} | {x["theme"]} | {x["cluster"]} | {x["theme"]} | 已收录 |' for x in radar)
        cluster_add = f"\n\n## {now:%Y-%m-%d} 007 运行\n\n- 覆盖：{', '.join(x[0] for x in CLUSTERS)}。\n- 新增去重候选：{len(radar)}。\n- 搜索与报告由 007 只读工作流生成。\n"
        writes = {md_path: "\n".join(lines), json_path: json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            ledger: old_ledger.rstrip() + "\n" + ledger_add + "\n", cluster_history: old_clusters.rstrip() + cluster_add}
        temps = []
        for path, content in writes.items():
            temp = path.with_name(path.name + ".tmp")
            temp.write_text(content, encoding="utf-8")
            temps.append((temp, path))
        for temp, path in temps:
            os.replace(temp, path)
        print(f"created: {md_path}, {json_path} ({len(radar)} candidates)")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAILED: {clean(exc, 500)}", file=sys.stderr)
        raise SystemExit(1)
