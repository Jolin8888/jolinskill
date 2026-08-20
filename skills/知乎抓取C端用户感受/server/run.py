#!/usr/bin/env python3
"""Build the daily Zhihu home-textile customer-voice report.

The runner deliberately uses small API pages. A report is written atomically
only after all three keywords have been collected and rendered successfully.
"""

import argparse
import datetime as dt
import fcntl
import html
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo


ZONE = ZoneInfo("America/New_York")
ZH = Path.home() / ".local/bin/zhihu"
OUT = Path.home() / "reports/zhihu-customer-voice"
LOG = Path.home() / ".local/state/zhihu-customer-voice"
KEYWORDS = ("枕芯", "被芯", "四件套")
SEARCH_PAGE_SIZE = 10
QUESTION_LIMIT = 10
ANSWER_LIMIT = 5
DEFAULT_MAX_SEARCH_PAGES = 12
DEFAULT_ANSWER_SEARCH_PAGES = 1
REPORT_MARKERS = (
    "## 关键词索引",
    "## 枕芯",
    "## 被芯",
    "## 四件套",
    "## 运行汇总",
    "## 数据质量说明",
)


def clean(value, limit=None):
    value = html.unescape(re.sub(r"<[^>]+>", "", str(value or "")))
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit] if limit is not None else value


def table_text(value):
    return clean(value).replace("|", "／")


def link_text(value):
    return table_text(value).replace("[", "【").replace("]", "】")


def visible_number(value):
    if value is None or isinstance(value, bool):
        return "不可见"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return "不可见"


def author_name(author):
    if not isinstance(author, dict):
        return "匿名用户"
    return clean(author.get("name") or "匿名用户", 60)


def parse_args():
    parser = argparse.ArgumentParser(description="Collect a complete Zhihu customer-voice report.")
    parser.add_argument("--date", help="America/New_York report date in YYYY-MM-DD")
    return parser.parse_args()


def report_path(report_date):
    return OUT / f"{report_date:%m%d}家纺c端客户抓取.md"


def validate_complete_report(path):
    if not path.is_file() or path.stat().st_size <= 1000:
        return False, "文件不存在或体积过小"
    text = path.read_text(encoding="utf-8")
    missing = [marker for marker in REPORT_MARKERS if marker not in text]
    if missing:
        return False, f"缺少结构标记：{'、'.join(missing)}"
    for keyword in KEYWORDS:
        start = text.find(f"## {keyword}")
        end = text.find("\n## ", start + 1)
        section = text[start : end if end >= 0 else None]
        pattern = r"^### \d+\. \[.+\]\(https://www\.zhihu\.com/question/\d+\)$"
        if not re.search(pattern, section, re.M):
            return False, f"{keyword} 缺少可追溯问题"
    return True, "结构完整"


def check_cli_status():
    result = subprocess.run(
        [str(ZH), "status"], text=True, capture_output=True, timeout=60, check=False
    )
    if result.returncode:
        raise RuntimeError(f"zhihu status 失败：{clean(result.stderr or result.stdout, 300)}")


@contextmanager
def authenticated_client():
    # Lazy imports keep pure rendering tests independent from pyzhihu-cli.
    from zhihu_cli.auth import cookie_str_to_dict, get_cookie_string
    from zhihu_cli.client import ZhihuClient

    cookie = get_cookie_string()
    if not cookie:
        raise RuntimeError("未找到有效的知乎登录 Cookie")
    with ZhihuClient(cookie_str_to_dict(cookie)) as client:
        yield client


class RateLimiter:
    def __init__(self, delay_seconds):
        self.delay_seconds = max(0.0, delay_seconds)
        self.last_call = None

    def call(self, function, *args, **kwargs):
        if self.last_call is not None and self.delay_seconds:
            remaining = self.delay_seconds - (time.monotonic() - self.last_call)
            if remaining > 0:
                time.sleep(remaining)
        try:
            return function(*args, **kwargs)
        finally:
            self.last_call = time.monotonic()


def extract_question(item):
    obj = item.get("object") or {}
    typ = obj.get("type")
    if typ == "answer":
        question = obj.get("question") or {}
        question_id = question.get("id")
        title = question.get("title") or question.get("name")
        answer_count = question.get("answer_count")
    elif typ == "question":
        question_id = obj.get("id")
        title = obj.get("title") or obj.get("name")
        answer_count = obj.get("answer_count")
    else:
        return None
    if not str(question_id or "").isdigit():
        return None
    return {
        "id": str(question_id),
        "title": clean(title, 140) or "无标题",
        "answer_count": visible_number(answer_count),
    }


def extract_answer(item, question_id):
    answer = item.get("object") or {}
    question = answer.get("question") or {}
    answer_id = answer.get("id")
    if (
        answer.get("type") != "answer"
        or str(question.get("id") or "") != str(question_id)
        or not str(answer_id or "").isdigit()
    ):
        return None
    return {
        "id": str(answer_id),
        "author": author_name(answer.get("author")),
        "voteup_count": visible_number(answer.get("voteup_count")),
        "comment_count": visible_number(answer.get("comment_count")),
        "excerpt": clean(answer.get("content") or answer.get("excerpt"), 80)
        or "无可见摘录",
    }


def collect_search_questions(client, limiter, keyword, max_pages):
    questions = []
    seen = set()
    answers_by_question = {}
    exhausted = False
    pages_used = 0
    for page in range(max_pages):
        payload = limiter.call(
            client.search,
            keyword,
            search_type="general",
            offset=page * SEARCH_PAGE_SIZE,
            limit=SEARCH_PAGE_SIZE,
        )
        pages_used += 1
        data = payload.get("data") or []
        for item in data:
            question = extract_question(item)
            if question and question["id"] not in seen and len(questions) < QUESTION_LIMIT:
                questions.append(question)
                seen.add(question["id"])
                answers_by_question[question["id"]] = []
            if question and question["id"] in seen:
                answer = extract_answer(item, question["id"])
                known_ids = {row["id"] for row in answers_by_question[question["id"]]}
                if answer and answer["id"] not in known_ids:
                    answers_by_question[question["id"]].append(answer)
        if len(questions) == QUESTION_LIMIT:
            return questions, answers_by_question, pages_used, exhausted
        paging = payload.get("paging") or {}
        if paging.get("is_end") is True or not data:
            exhausted = True
            break
    return questions, answers_by_question, pages_used, exhausted


def collect_keyword(client, limiter, keyword, max_pages, answer_search_pages=1):
    questions, answers_by_question, pages_used, exhausted = collect_search_questions(
        client, limiter, keyword, max_pages
    )
    if not questions:
        raise RuntimeError(f"{keyword} 搜索未返回可验证的问题")

    collected = []
    for question in questions:
        answers = list(answers_by_question.get(question["id"], []))
        known_ids = {answer["id"] for answer in answers}
        for page in range(answer_search_pages):
            if len(answers) >= ANSWER_LIMIT:
                break
            answer_payload = limiter.call(
                client.search,
                question["title"],
                search_type="general",
                offset=page * SEARCH_PAGE_SIZE,
                limit=SEARCH_PAGE_SIZE,
            )
            data = answer_payload.get("data") or []
            for item in data:
                answer = extract_answer(item, question["id"])
                if answer and answer["id"] not in known_ids:
                    answers.append(answer)
                    known_ids.add(answer["id"])
                if len(answers) == ANSWER_LIMIT:
                    break
            paging = answer_payload.get("paging") or {}
            if paging.get("is_end") is True or not data:
                break
        collected.append(
            {
                "id": question["id"],
                "title": question["title"],
                "topics": [keyword],
                "answer_count": question["answer_count"],
                "answers": answers[:ANSWER_LIMIT],
            }
        )
    return {
        "keyword": keyword,
        "questions": collected,
        "pages_used": pages_used,
        "search_exhausted": exhausted,
    }


def render_keyword(result):
    keyword = result["keyword"]
    lines = [f"## {keyword}", ""]
    for index, question in enumerate(result["questions"], start=1):
        question_url = f"https://www.zhihu.com/question/{question['id']}"
        topics = "、".join(question["topics"]) or "未显示"
        lines.extend(
            [
                f"### {index}. [{link_text(question['title'])}]({question_url})",
                "",
                f"- 主题：{table_text(topics)}",
                f"- 可见回答总数：{question['answer_count']}；本次收录：{len(question['answers'])} 条",
                "",
                "| 回答 | 作者 | 赞同 | 评论 | 主题 | 短摘录 |",
                "|---|---|---:|---:|---|---|",
            ]
        )
        if question["answers"]:
            for answer_index, answer in enumerate(question["answers"], start=1):
                answer_url = f"{question_url}/answer/{answer['id']}"
                lines.append(
                    f"| [回答 {answer_index}]({answer_url}) | "
                    f"{table_text(answer['author'])} | {answer['voteup_count']} | "
                    f"{answer['comment_count']} | {table_text(topics)} | "
                    f"{table_text(answer['excerpt'])} |"
                )
        else:
            lines.append(
                "| - | 暂无可见回答 | 不可见 | 不可见 | "
                + table_text(topics)
                + " | - |"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


def render_report(report_date, collected_at, results, max_pages, answer_search_pages=1):
    question_total = sum(len(result["questions"]) for result in results)
    answer_total = sum(
        len(question["answers"])
        for result in results
        for question in result["questions"]
    )
    backfill_note = (
        "本报告为补跑，数据反映实际采集时点，非原计划时点快照。"
        if report_date != collected_at.date()
        else "本报告为当日定时采集。"
    )
    lines = [
        f"# {report_date:%m%d} 家纺 C 端用户感受",
        "",
        f"- 报告日期：{report_date.isoformat()} America/New_York",
        f"- 实际采集：{collected_at:%Y-%m-%d %H:%M:%S} America/New_York",
        "- 运行位置：007 服务器",
        f"- 关键词：{'、'.join(KEYWORDS)}",
        "",
        "## 关键词索引",
        "",
        *[f"- [{result['keyword']}](#{result['keyword']})" for result in results],
        "",
    ]
    for result in results:
        lines.extend([render_keyword(result), ""])
    lines.extend(
        [
            "## 运行汇总",
            "",
            f"- 共收录 {question_total} 个可追溯问题、{answer_total} 条可见回答。",
        ]
    )
    for result in results:
        lines.append(
            f"- {result['keyword']}：{len(result['questions'])} 个问题，"
            f"{sum(len(question['answers']) for question in result['questions'])} 条回答，"
            f"搜索 {result['pages_used']} 页。"
        )
    lines.extend(
        [
            "",
            "## 数据质量说明",
            "",
            f"- {backfill_note}",
            "- 每次搜索请求仅取 10 条，按结果顺序去重收集前 10 个问题；每个问题最多收录 5 条当时可见回答。",
            f"- 搜索最多翻阅 {max_pages} 页；若结果提前结束或问题不足，汇总按实际可验证数量记录。",
            f"- 问题详情接口当前被知乎拒绝；因此每个问题仅通过可用的搜索接口再检索最多 {answer_search_pages} 页，并严格按问题 ID 聚合回答。",
            "- “主题”为本次搜索关键词归类，不声称是知乎官方话题标签。",
            "- 赞同、评论或主题字段未由知乎接口返回时标记为“不可见”或“未显示”，不推测、不补造。",
            "- 短摘录由当时可见回答清理而来，每条不超过 80 个字符；不复制回答全文。",
            "- 遇到登录失效、403/安全验证、网络或数据解析异常时，本次运行立即失败且不写入部分报告。",
            "",
        ]
    )
    return "\n".join(lines)


def main():
    args = parse_args()
    now = dt.datetime.now(ZONE)
    target_date = dt.date.fromisoformat(args.date) if args.date else now.date()
    target = report_path(target_date)
    OUT.mkdir(parents=True, exist_ok=True)
    LOG.mkdir(parents=True, exist_ok=True)

    with (LOG / "run.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another run is active", file=sys.stderr)
            return 75

        complete, reason = validate_complete_report(target)
        if complete:
            print(f"already complete and verified: {target}")
            return 0
        if target.exists():
            print(f"existing report is incomplete and will be replaced atomically: {reason}", file=sys.stderr)

        check_cli_status()
        delay = float(os.environ.get("ZHIHU_REQUEST_DELAY_SECONDS", "2.0"))
        max_pages = int(os.environ.get("ZHIHU_MAX_SEARCH_PAGES", str(DEFAULT_MAX_SEARCH_PAGES)))
        answer_search_pages = int(
            os.environ.get("ZHIHU_ANSWER_SEARCH_PAGES", str(DEFAULT_ANSWER_SEARCH_PAGES))
        )
        if max_pages < 1 or max_pages > 30:
            raise RuntimeError("ZHIHU_MAX_SEARCH_PAGES 必须在 1 到 30 之间")
        if answer_search_pages < 1 or answer_search_pages > 3:
            raise RuntimeError("ZHIHU_ANSWER_SEARCH_PAGES 必须在 1 到 3 之间")
        limiter = RateLimiter(delay)
        results = []
        with authenticated_client() as client:
            for keyword in KEYWORDS:
                results.append(
                    collect_keyword(client, limiter, keyword, max_pages, answer_search_pages)
                )

        report = render_report(
            target_date, now, results, max_pages, answer_search_pages
        )
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(report, encoding="utf-8")
        valid, reason = validate_complete_report(temp)
        if not valid:
            temp.unlink(missing_ok=True)
            raise RuntimeError(f"报告写入前完整性校验失败：{reason}")
        os.replace(temp, target)
        print(f"created and verified: {target}")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAILED: {clean(exc, 500)}", file=sys.stderr)
        raise SystemExit(1)
