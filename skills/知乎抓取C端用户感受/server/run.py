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
    "# 国内家纺C端客户需求汇总",
    "## 一、今日核心判断",
    "## 二、三类产品需求观察",
    "## 三、消费者共同关注点",
    "## 四、建议优先动作",
    "## 五、原始问题与回答明细",
    "### 枕芯原始明细",
    "### 被芯原始明细",
    "### 四件套原始明细",
    "## 六、采集概况与数据说明",
)

CONCERN_RULES = (
    (
        "适合性与使用场景",
        ("适合", "怎么选", "如何选", "哪种", "选择", "推荐", "舒服", "舒适", "好睡"),
        "消费者想知道什么人、什么季节和什么睡眠习惯适合这款产品。",
    ),
    (
        "价格与性价比",
        ("价格", "贵", "便宜", "性价比", "几十", "几百", "利润", "预算", "一两百"),
        "消费者愿意为真实差异付费，但需要看懂价格具体贵在哪里。",
    ),
    (
        "材质与填充",
        ("材质", "填充", "纤维", "棉", "羽绒", "蚕丝", "乳胶", "天丝", "面料"),
        "消费者面对材料名词容易混乱，需要优缺点和适用人群对照。",
    ),
    (
        "睡眠健康与体感",
        ("颈", "睡眠", "儿童", "孩子", "宝宝", "螨虫", "发霉", "透气", "闷", "清凉"),
        "消费者关注支撑、透气、温度和贴身安全，而不只是外观。",
    ),
    (
        "清洗、耐用与售后使用",
        ("清洗", "换一次", "耐用", "缩水", "起球", "贴合", "乱跑"),
        "消费者在意产品买回家以后是否好维护、容易变形或影响使用。",
    ),
    (
        "规格参数与组合方式",
        ("尺寸", "支数", "密度", "床笠", "床单", "四件套", "三件套", "单件"),
        "消费者需要真实参数和更灵活的规格组合，避免买错尺寸或闲置组件。",
    ),
)

CATEGORY_GUIDANCE = {
    "枕芯": {
        "judgment": "消费者更关心高度、软硬度和睡姿是否匹配，其次才是材料与品牌。价格更高不等于更适合。",
        "needs": "重点说明仰睡、侧睡和混合睡姿对应的受压后高度，并公开软硬度、回弹、透气、可清洁性和适合人群；儿童款突出防潮与安全。",
    },
    "被芯": {
        "judgment": "被芯没有绝对最好的材料，选择主要受地区温度、怕冷怕热、重量偏好、预算和清洁方式影响。",
        "needs": "按南方湿冷、北方干冷、空调房和春秋过渡等场景划分产品，说明填充物含量、填充重量、适用温度、贴合度和洗护方式。",
    },
    "四件套": {
        "judgment": "消费者最关心面料和工艺是否与价格匹配，并对虚报支数、模糊成分和品牌溢价保持警惕。",
        "needs": "公开面料成分、支数、密度、织法、安全等级和洗后表现；提供床单款、床笠款、租房基础款、夏季凉感款及单件组合。",
    },
}


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


def numeric_value(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def chinese_date(value):
    return f"{value.year}年{value.month:02d}月{value.day:02d}日"


def chinese_datetime(value):
    return (
        f"{chinese_date(value)} "
        f"{value.hour:02d}时{value.minute:02d}分{value.second:02d}秒"
    )


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
        start = text.find(f"### {keyword}原始明细")
        end = text.find("\n### ", start + 1)
        section = text[start : end if end >= 0 else None]
        pattern = r"^#### \d+\. \[.+\]\(https://www\.zhihu\.com/question/\d+\)$"
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


def question_interactions(question):
    votes = sum(numeric_value(answer["voteup_count"]) for answer in question["answers"])
    comments = sum(numeric_value(answer["comment_count"]) for answer in question["answers"])
    return votes, comments


def question_score(question):
    votes, comments = question_interactions(question)
    return votes + comments * 2


def top_questions(result, limit=3):
    return sorted(result["questions"], key=question_score, reverse=True)[:limit]


def concern_counts(results):
    counts = {name: 0 for name, _, _ in CONCERN_RULES}
    for result in results:
        for question in result["questions"]:
            source = " ".join(
                [question["title"]]
                + [answer["excerpt"] for answer in question["answers"]]
            )
            for name, terms, _ in CONCERN_RULES:
                if any(term in source for term in terms):
                    counts[name] += 1
    return counts


def render_category_observation(result):
    keyword = result["keyword"]
    guidance = CATEGORY_GUIDANCE[keyword]
    answer_count = sum(len(question["answers"]) for question in result["questions"])
    lines = [
        f"### {keyword}",
        "",
        f"- 今日样本：{len(result['questions'])} 个问题、{answer_count} 条可见回答。",
        f"- 核心判断：{guidance['judgment']}",
        f"- 产品表达重点：{guidance['needs']}",
        "- 当日高关注问题：",
    ]
    for question in top_questions(result):
        votes, comments = question_interactions(question)
        question_url = f"https://www.zhihu.com/question/{question['id']}"
        lines.append(
            f"  - [{link_text(question['title'])}]({question_url})"
            f"：样本内可见赞同 {votes}，评论 {comments}。"
        )
    lines.append("")
    return "\n".join(lines).rstrip()


def render_keyword_details(result):
    keyword = result["keyword"]
    answer_count = sum(len(question["answers"]) for question in result["questions"])
    lines = [
        f"### {keyword}原始明细",
        "",
        f"共 {len(result['questions'])} 个问题、{answer_count} 条当时可见回答。",
        "",
    ]
    for index, question in enumerate(result["questions"], start=1):
        question_url = f"https://www.zhihu.com/question/{question['id']}"
        votes, comments = question_interactions(question)
        lines.extend(
            [
                f"#### {index}. [{link_text(question['title'])}]({question_url})",
                "",
                f"- 本次收录：{len(question['answers'])} 条可见回答。",
                f"- 样本互动：赞同 {votes}，评论 {comments}。",
                "",
                "| 回答链接 | 作者 | 赞同数 | 评论数 | 可见短摘录 |",
                "|---|---|---:|---:|---|",
            ]
        )
        if question["answers"]:
            for answer_index, answer in enumerate(question["answers"], start=1):
                answer_url = f"{question_url}/answer/{answer['id']}"
                lines.append(
                    f"| [回答 {answer_index}]({answer_url}) | "
                    f"{table_text(answer['author'])} | {answer['voteup_count']} | "
                    f"{answer['comment_count']} | {table_text(answer['excerpt'])} |"
                )
        else:
            lines.append("| - | 暂无可见回答 | 不可见 | 不可见 | - |")
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
    counts = concern_counts(results)
    ranked_concerns = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    lines = [
        "# 国内家纺C端客户需求汇总",
        "",
        f"- 报告日期：{chinese_date(report_date)}",
        f"- 实际采集：美国纽约时间 {chinese_datetime(collected_at)}",
        f"- 调研范围：{'、'.join(KEYWORDS)}",
        f"- 样本数量：{question_total} 个问题、{answer_total} 条当时可见回答",
        "",
        "## 一、今日核心判断",
        "",
        "1. 消费者最缺的不是更多材料名称，而是一套简单、可信的选择方法。",
        "2. 消费者愿意为真实差异付费，但需要看懂不同价格具体贵在哪里。",
        "3. 枕芯、被芯和四件套都存在明显的适配问题；能否降低买错风险，比单纯强调高端更重要。",
        "4. 产品页面应优先讲清适合谁、适合什么场景、关键参数、使用限制和洗护方式。",
        "",
        "## 二、三类产品需求观察",
        "",
    ]
    for result in results:
        lines.extend([render_category_observation(result), ""])
    lines.extend(
        [
            "## 三、消费者共同关注点",
            "",
            "| 关注主题 | 涉及问题数 | 对产品的启示 |",
            "|---|---:|---|",
        ]
    )
    descriptions = {name: description for name, _, description in CONCERN_RULES}
    for name, count in ranked_concerns:
        if count:
            lines.append(f"| {name} | {count} | {descriptions[name]} |")
    lines.extend(
        [
            "",
            "## 四、建议优先动作",
            "",
            "1. 为每款产品增加“适合谁、不适合谁、适合什么季节”的场景说明。",
            "2. 用面料、填充、工艺、耐用性和售后解释价格差异，减少空泛的高端概念。",
            "3. 分别建立枕芯高度与睡姿、被芯温度与重量、四件套面料与季节的中文选择表。",
            "4. 优先解决消费者买回家后的问题，包括塌陷、闷热、被芯不贴套、缩水起球和规格不合。",
            "5. 内容选题优先回答“不同价格差在哪里”“什么人适合什么材质”“怎样避免买错”。",
            "",
            "## 五、原始问题与回答明细",
            "",
            "以下明细用于追溯结论来源。为保证可读性，重要判断已放在报告前部。",
            "",
        ]
    )
    for result in results:
        lines.extend([render_keyword_details(result), ""])
    lines.extend(
        [
            "## 六、采集概况与数据说明",
            "",
            f"- 共收录 {question_total} 个可追溯问题、{answer_total} 条当时可见回答。",
        ]
    )
    for result in results:
        lines.append(
            f"- {result['keyword']}：{len(result['questions'])} 个问题，"
            f"{sum(len(question['answers']) for question in result['questions'])} 条回答，"
            f"检索 {result['pages_used']} 页。"
        )
    lines.extend(
        [
            "",
            f"- {backfill_note}",
            "- 每次检索请求仅取 10 条，按结果顺序去重收集前 10 个问题；每个问题最多收录 5 条当时可见回答。",
            f"- 检索最多翻阅 {max_pages} 页；若结果提前结束或问题不足，按实际可验证数量记录。",
            f"- 知乎问题详情接口当前不可用，因此不再显示容易误解的“回答总数为零”；每个问题通过可用搜索接口再检索最多 {answer_search_pages} 页，并严格按问题编号聚合回答。",
            "- 赞同数、评论数未返回时标记为“不可见”，不推测、不补造。",
            "- 短摘录由当时可见回答清理而来，每条不超过 80 个字符；不复制回答全文。",
            "- 部分回答可能带有品牌推广倾向，互动数不等于实际购买量；本报告用于发现需求与表达机会，不单独用于估算市场规模。",
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
