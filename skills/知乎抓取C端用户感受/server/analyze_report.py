#!/usr/bin/env python3
"""Enrich a verified Zhihu report with evidence-bound Claude analysis."""

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo


ZONE = ZoneInfo("America/New_York")
REPORTS = Path.home() / "reports/zhihu-customer-voice"
STATE = Path.home() / ".local/state/zhihu-customer-voice/claude-analysis"
BRAND_PROFILE = Path(__file__).with_name("knowledge") / "梦莱儿C端品牌认知.md"
MODEL_DEFAULT = "claude-opus-5"
PROMPT_VERSION = "zhihu-menglai-v2"
START_PREFIX = "<!-- CLAUDE_ANALYSIS_START"
END_MARKER = "<!-- CLAUDE_ANALYSIS_END -->"
INSERT_BEFORE = "## 五、原始问题与回答明细"
KEYWORDS = ("枕芯", "被芯", "四件套")
ANALYSIS_PATTERN = re.compile(
    r"\n?<!-- CLAUDE_ANALYSIS_START[^\n]*-->.*?"
    r"<!-- CLAUDE_ANALYSIS_END -->\n?",
    re.S,
)
QUESTION_PATTERN = re.compile(
    r"^#### \d+\. \[(?P<title>.+?)\]\((?P<url>https://www\.zhihu\.com/question/\d+)\)\n"
    r"(?P<body>.*?)(?=^#### \d+\.|^### (?:枕芯|被芯|四件套)原始明细|^## |\Z)",
    re.M | re.S,
)
ANSWER_ROW_PATTERN = re.compile(
    r"^\| \[回答 \d+\]\((?P<url>https://www\.zhihu\.com/question/\d+/answer/\d+)\)"
    r" \| (?P<author>.*?) \| (?P<votes>.*?) \| (?P<comments>.*?) \| (?P<excerpt>.*?) \|$",
    re.M,
)


def parse_args():
    parser = argparse.ArgumentParser(description="Add Opus analysis to one daily report.")
    parser.add_argument("--date", help="America/New_York report date in YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Build and validate input only")
    parser.add_argument("--force", action="store_true", help="Replace an existing analysis block")
    return parser.parse_args()


def required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is missing")
    return value


def report_path(report_date):
    return REPORTS / f"{report_date:%m%d}家纺c端客户抓取.md"


def source_text(text):
    cleaned = ANALYSIS_PATTERN.sub("\n", text)
    cleaned = re.sub(
        rf"\n{{3,}}(?={re.escape(INSERT_BEFORE)})", "\n\n", cleaned
    )
    return cleaned.rstrip() + "\n"


def digest_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_number(value):
    match = re.search(r"\d+", str(value))
    return int(match.group()) if match else 0


def category_at(text, position):
    found = ""
    found_at = -1
    for keyword in KEYWORDS:
        marker_at = text.rfind(f"### {keyword}原始明细", 0, position)
        if marker_at > found_at:
            found, found_at = keyword, marker_at
    return found or "未分类"


def report_evidence(text):
    evidence = []
    for match in QUESTION_PATTERN.finditer(text):
        body = match.group("body")
        answers = []
        for row in ANSWER_ROW_PATTERN.finditer(body):
            answers.append(
                {
                    "url": row.group("url"),
                    "author": row.group("author").strip(),
                    "votes": parse_number(row.group("votes")),
                    "comments": parse_number(row.group("comments")),
                    "excerpt": row.group("excerpt").strip()[:80],
                }
            )
        evidence.append(
            {
                "category": category_at(text, match.start()),
                "title": match.group("title"),
                "url": match.group("url"),
                "answers": answers,
                "score": sum(a["votes"] + a["comments"] * 2 for a in answers),
            }
        )
    return evidence


def select_balanced(evidence, limit):
    selected = []
    per_category = max(1, math.ceil(limit / len(KEYWORDS)))
    for keyword in KEYWORDS:
        rows = [row for row in evidence if row["category"] == keyword]
        selected.extend(sorted(rows, key=lambda row: row["score"], reverse=True)[:per_category])
    if len(selected) < limit:
        selected_urls = {row["url"] for row in selected}
        remaining = [row for row in evidence if row["url"] not in selected_urls]
        selected.extend(sorted(remaining, key=lambda row: row["score"], reverse=True)[: limit - len(selected)])
    return selected[:limit]


def compact_evidence(text, question_limit, answers_per_question):
    evidence = select_balanced(report_evidence(text), question_limit)
    lines = []
    current_category = None
    for row in evidence:
        if row["category"] != current_category:
            current_category = row["category"]
            lines.extend([f"### {current_category}", ""])
        votes = sum(answer["votes"] for answer in row["answers"])
        comments = sum(answer["comments"] for answer in row["answers"])
        lines.append(
            f"- [{row['title']}]({row['url']})｜样本内可见赞同 {votes}，评论 {comments}"
        )
        ranked_answers = sorted(
            row["answers"], key=lambda answer: answer["votes"] + answer["comments"] * 2, reverse=True
        )[:answers_per_question]
        for answer in ranked_answers:
            lines.append(
                f"  - {answer['author']}｜赞同 {answer['votes']}，评论 {answer['comments']}｜"
                f"{answer['excerpt']}｜{answer['url']}"
            )
    return "\n".join(lines).strip()


def date_for_filename(path, target_date):
    match = re.match(r"(?P<month>\d{2})(?P<day>\d{2})家纺c端客户抓取\.md$", path.name)
    if not match:
        return None
    value = dt.date(target_date.year, int(match.group("month")), int(match.group("day")))
    if value > target_date and (value - target_date).days > 180:
        value = dt.date(target_date.year - 1, value.month, value.day)
    return value


def historical_evidence(target_date, history_days):
    candidates = []
    for path in REPORTS.glob("[0-1][0-9][0-3][0-9]家纺c端客户抓取.md"):
        report_date = date_for_filename(path, target_date)
        if report_date and report_date < target_date:
            candidates.append((report_date, path))
    candidates.sort()
    candidates = candidates[-history_days:]
    digests = []
    for report_date, path in candidates:
        text = source_text(path.read_text(encoding="utf-8"))
        compact = compact_evidence(text, question_limit=12, answers_per_question=1)
        digests.append(f"## {report_date.isoformat()}\n\n{compact}")
    return candidates, "\n\n".join(digests)


def read_prior_summary(target_date):
    path = STATE / "history-summary.json"
    if not path.is_file():
        return "尚无历史滚动摘要；本次需要从历史报告建立基线。"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        generated_for = dt.date.fromisoformat(payload["generated_for"])
        summary = str(payload["summary_markdown"]).strip()
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return "历史滚动摘要不可用；本次仅使用可验证的历史报告重建。"
    if generated_for > target_date or not summary:
        return "现有滚动摘要晚于目标日期，本次不使用，避免未来数据泄漏。"
    return summary


def build_prompt(
    report_date,
    current_text,
    history_text,
    prior_summary,
    brand_profile,
    evidence_start,
):
    next_year = report_date.year + 1
    headings = (
        "## 消费者购买心理行为梳理",
        f"## {next_year}年春夏新品产品上市建议",
        "## 梦莱儿推广渠道与内容导航",
        "## 分析口径与证据边界",
    )
    return f"""请基于下方证据，为《国内家纺C端客户需求汇总》生成可直接发布的中文分析。

报告日期：{report_date.isoformat()}
目标上市季：{next_year}年春夏
证据时间窗：{evidence_start} 至 {report_date.isoformat()}

必须遵守：
1. 只输出一个 JSON 对象，不要使用代码围栏。字段只有 analysis_markdown、history_summary_markdown、evidence_window。
2. analysis_markdown 必须严格依次使用以下四个二级标题：
{chr(10).join(headings)}
3. 第一部分按“购买任务/触发—顾虑—比较标准—决策阻力—成交信号—购后风险”梳理，并区分当天新信号、跨日持续趋势、待验证假设。
4. 第二部分提出面向 {next_year} 年春夏的新品组合、关键产品定义、上市节奏、线下验证与线上验证。每项写清需求依据、建议动作、验证指标；不知道的价格、材料或能力必须标为待验证，不得补造。
5. 第三部分以梦莱儿的已确认定位为边界，分别说明大润发线下、天猫、小红书在认知、比较、体验、成交、复购中的职责，并给出内容栏目、用户问题导航和转化路径。
6. 第三部分必须包含“### 用途一：品牌定位优化”和“### 用途二：分品类日常内容卖点”两个三级标题：
   - 梦莱儿已经确认的定位锚点是“懂她疲惫的那个人”，不得另提学生党、儿童家长、年龄层等备选品牌定位，不得用人群切片取代品牌人格。用途一只能检验当天与历史证据如何支持或挑战该锚点，并优化三件事：一句话定位表达、触温托静的产品证明、大润发/天猫/小红书如何一致落地。写清仍缺什么产品和经营证据。
   - 学生、租房、儿童家庭等只可作为具体商品线或内容选题人群，不能升级为梦莱儿整体品牌定位。
   - 用途二必须再依次使用“#### 枕芯”“#### 被芯”“#### 四件套”。每个品类给出 2—3 条当日优先卖点，按“用户问题或场景 + 消费者收益 + 产品机理/参数证据 + 适用边界”写成。随后分别给出：小红书标题与正文切入、微信贴图主标题与副标题、公众号选题与内容提纲。缺少真实产品参数时写“需产品资料验证”，禁止把消费者需求直接说成梦莱儿已有功能。
7. 小红书、微信贴图和公众号要做平台化改写：小红书负责场景种草与选择教育；微信贴图只传达一个核心卖点；公众号负责完整解释、对比与信任建立。不要把同一句文案复制三遍。
8. 所有重要判断都要紧邻至少一个 Markdown 格式的知乎问题链接；只允许引用证据区已有 URL。互动数只能写成“样本内可见互动”，不能等同销量、市场规模或购买率。
9. 全文使用清晰中文，先写结论再写依据；明确标注“样本事实”“分析推断”“待验证”。不得编造梦莱儿现有产品力、经营数据或消费者身份。所有示例文案均标明“产品资料验证前不可直接发布”，不得承诺“睡醒不酸、一定不闷、就够好”等未验证效果；单个知乎回答中的价格只能称为样本观点，不能写成市场定价事实。
   - 梦莱儿内容不得使用“我试了、亲测、实测”等第一人称体验，除非证据中确有梦莱儿完成的真实测试。
   - 标题不得使用“别被忽悠、智商税、不花冤枉钱、踩坑”等攻击或焦虑表达。应把同一问题改写成温柔、平视的选择帮助，例如“被芯材质很多，慢慢选适合你的”。
10. history_summary_markdown 用不超过 1800 个汉字更新滚动洞察，保留稳定趋势、变化信号、矛盾和证据缺口，不写营销套话。
11. evidence_window 返回对象，包含 current_date、history_start、history_end 三个字符串。
12. 第三部分末尾必须增加“### 品牌人格一致性自检”，逐条回答：是否在释放焦虑、是否在看见她而非要求她、是否让疲惫的人喘口气。任一项不通过就先重写相关文案，最终只能输出三项均通过的版本。
13. “分析口径与证据边界”必须准确写明证据窗口为 {evidence_start} 至 {report_date.isoformat()}，不能自行缩短或扩大。
14. 控制篇幅：消费心理 700—1000 字；新品建议 900—1300 字；梦莱儿品牌定位与分品类内容 2200—3200 字；证据边界 250—450 字；analysis_markdown 总计不超过 6500 个汉字。每条建议先给结论，再用最少必要文字解释。

【梦莱儿已确认信息与边界】
{brand_profile}

【上一版历史滚动摘要】
{prior_summary}

【当天完整压缩证据】
{current_text}

【最近历史报告压缩证据补充】
{history_text or '历史趋势已包含在滚动摘要中'}
"""


def build_history_prompt(report_date, history_text):
    return f"""请只根据下方过去报告证据，建立一份中文滚动洞察摘要，供 {report_date.isoformat()} 的家纺日报继续分析。

要求：
1. 只输出符合指定 JSON Schema 的对象。
2. history_summary_markdown 控制在 700—1200 个汉字。
3. 依次概括：稳定的购买心理、枕芯/被芯/四件套的持续需求、变化或矛盾信号、对梦莱儿“触温托静”产品力的启示、证据缺口。
4. 重要趋势必须保留 Markdown 格式的知乎问题链接，至少引用 6 个不同问题；只能使用证据中已有 URL。
5. 明确区分样本事实、分析推断和待验证，不把知乎互动当成销量或市场规模。

【过去报告压缩证据】
{history_text}
"""


def recent_history_excerpt(history_text, limit=2):
    parts = [
        part.strip()
        for part in re.split(r"(?=^## \d{4}-\d{2}-\d{2}$)", history_text, flags=re.M)
        if part.strip()
    ]
    return "\n\n".join(parts[-limit:])


def messages_endpoint(base_url):
    base = base_url.rstrip("/")
    return f"{base}/messages" if base.endswith("/v1") else f"{base}/v1/messages"


def structured_output_schema():
    return {
        "type": "object",
        "properties": {
            "analysis_markdown": {"type": "string"},
            "history_summary_markdown": {"type": "string"},
            "evidence_window": {
                "type": "object",
                "properties": {
                    "current_date": {"type": "string"},
                    "history_start": {"type": "string"},
                    "history_end": {"type": "string"},
                },
                "required": ["current_date", "history_start", "history_end"],
                "additionalProperties": False,
            },
        },
        "required": [
            "analysis_markdown",
            "history_summary_markdown",
            "evidence_window",
        ],
        "additionalProperties": False,
    }


def history_output_schema():
    return {
        "type": "object",
        "properties": {"history_summary_markdown": {"type": "string"}},
        "required": ["history_summary_markdown"],
        "additionalProperties": False,
    }


def request_claude(prompt, timeout=240, schema=None, max_tokens_override=None):
    base_url = required_env("ANTHROPIC_BASE_URL")
    api_key = required_env("ANTHROPIC_API_KEY")
    model = os.environ.get("CLAUDE_ANALYSIS_MODEL", MODEL_DEFAULT).strip() or MODEL_DEFAULT
    max_tokens = (
        max_tokens_override
        if max_tokens_override is not None
        else int(os.environ.get("CLAUDE_ANALYSIS_MAX_TOKENS", "8000"))
    )
    auth_style = os.environ.get("CLAUDE_AUTH_STYLE", "x-api-key").strip().lower()
    if auth_style not in {"x-api-key", "bearer", "both"}:
        raise RuntimeError("CLAUDE_AUTH_STYLE must be x-api-key, bearer, or both")
    headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
    if auth_style in {"x-api-key", "both"}:
        headers["x-api-key"] = api_key
    if auth_style in {"bearer", "both"}:
        headers["Authorization"] = f"Bearer {api_key}"
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": "你是中国家纺消费洞察与商品企划负责人。严守证据边界，输出可执行、可验证的中文决策分析。",
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": schema or structured_output_schema(),
            }
        },
    }
    request = urllib.request.Request(
        messages_endpoint(base_url),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            stop_reason = str(payload.get("stop_reason") or "")
            if stop_reason not in {"end_turn", "stop_sequence"}:
                raise RuntimeError(
                    f"Claude API stopped before complete output: {stop_reason or 'unknown'}"
                )
            blocks = payload.get("content") or []
            text = "\n".join(
                block.get("text", "") for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            ).strip()
            if not text:
                raise RuntimeError("Claude response contained no text block")
            return text, model
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            last_error = RuntimeError(f"Claude API HTTP {exc.code}: {detail}")
            if exc.code not in {429, 500, 502, 503, 504}:
                break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = RuntimeError(f"Claude API request failed: {type(exc).__name__}: {exc}")
        if attempt < 2:
            time.sleep(5 * (attempt + 1))
    raise last_error or RuntimeError("Claude API request failed")


def parse_json_object(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S)
    decoder = json.JSONDecoder()
    start = raw.find("{")
    if start < 0:
        raise RuntimeError("Claude response did not contain a JSON object")
    try:
        payload, _ = decoder.raw_decode(raw[start:])
    except json.JSONDecodeError as exc:
        repaired = repair_inner_quotes(raw[start:])
        try:
            payload, _ = decoder.raw_decode(repaired)
        except json.JSONDecodeError:
            prefix = repr(raw[:240])
            suffix = repr(raw[-240:])
            raise RuntimeError(
                f"Claude response JSON was invalid; chars={len(raw)}; "
                f"prefix={prefix}; suffix={suffix}"
            ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Claude response root must be an object")
    return payload


def repair_inner_quotes(raw):
    """Repair relay responses that fail to escape plain quotes inside JSON strings.

    A quote closes a JSON string only when the next non-space character is a
    structural delimiter. All other unescaped quotes are preserved as content
    by escaping them. The repaired object is still parsed and fully validated.
    """
    output = []
    in_string = False
    escaped = False
    for index, char in enumerate(raw):
        if not in_string:
            output.append(char)
            if char == '"':
                in_string = True
            continue
        if escaped:
            output.append(char)
            escaped = False
            continue
        if char == "\\":
            output.append(char)
            escaped = True
            continue
        if char != '"':
            output.append(char)
            continue
        following = index + 1
        while following < len(raw) and raw[following].isspace():
            following += 1
        next_char = raw[following] if following < len(raw) else ""
        if next_char in {":", ",", "}", "]", ""}:
            output.append(char)
            in_string = False
        else:
            output.append('\\"')
    return "".join(output)


def validate_output(payload, allowed_urls, report_date, evidence_start=None):
    analysis = str(payload.get("analysis_markdown") or "").strip()
    summary = str(payload.get("history_summary_markdown") or "").strip()
    headings = (
        "## 消费者购买心理行为梳理",
        f"## {report_date.year + 1}年春夏新品产品上市建议",
        "## 梦莱儿推广渠道与内容导航",
        "## 分析口径与证据边界",
    )
    positions = []
    for heading in headings:
        if analysis.count(heading) != 1:
            raise RuntimeError(f"Claude analysis heading is missing or duplicated: {heading}")
        positions.append(analysis.index(heading))
    if positions != sorted(positions):
        raise RuntimeError("Claude analysis headings are out of order")
    required_content_markers = (
        "### 用途一：品牌定位优化",
        "### 用途二：分品类日常内容卖点",
        "#### 枕芯",
        "#### 被芯",
        "#### 四件套",
        "小红书",
        "微信贴图",
        "公众号",
        "需产品资料验证",
        "### 品牌人格一致性自检",
    )
    missing_content = [marker for marker in required_content_markers if marker not in analysis]
    if missing_content:
        raise RuntimeError(
            "Claude analysis is missing brand/content structure: "
            + "、".join(missing_content)
        )
    if len(analysis) < 1200 or len(analysis) > 18000:
        raise RuntimeError("Claude analysis length is outside the accepted range")
    chinese = len(re.findall(r"[\u4e00-\u9fff]", analysis))
    latin = len(re.findall(r"[A-Za-z]", analysis))
    if chinese < 600 or chinese < latin * 2:
        raise RuntimeError("Claude analysis is not sufficiently Chinese")
    used_urls = set(re.findall(r"https://www\.zhihu\.com/question/\d+(?:/answer/\d+)?", analysis))
    if len(used_urls) < 4:
        raise RuntimeError("Claude analysis has too few traceable Zhihu citations")
    unknown = used_urls - allowed_urls
    if unknown:
        raise RuntimeError("Claude analysis cited URLs outside the supplied evidence")
    for index, heading in enumerate(headings[:3]):
        start = analysis.index(heading)
        end = analysis.index(headings[index + 1])
        section_urls = re.findall(r"https://www\.zhihu\.com/question/\d+", analysis[start:end])
        if not section_urls:
            raise RuntimeError(f"Claude analysis section has no evidence link: {heading}")
    if evidence_start:
        boundary = analysis[analysis.index(headings[3]):]
        if evidence_start not in boundary or report_date.isoformat() not in boundary:
            raise RuntimeError("Claude evidence boundary does not match the supplied window")
    brand_section = analysis[analysis.index(headings[2]):analysis.index(headings[3])]
    content_start = brand_section.index("### 用途二：分品类日常内容卖点")
    content_end = brand_section.index("### 品牌人格一致性自检")
    content_section = brand_section[content_start:content_end]
    publish_copy_lines = [
        line
        for line in content_section.splitlines()
        if any(
            label in line
            for label in ("标题：", "主标题：", "副标题：", "正文切入：")
        )
    ]
    forbidden_publish_phrases = (
        "我试了",
        "亲测",
        "实测",
        "别被忽悠",
        "智商税",
    )
    forbidden_content_claims = (
        "不花冤枉钱",
        "睡醒脖子不酸",
        "醒来脖子不僵",
        "一定不闷",
        "就够好",
    )
    publish_copy = "\n".join(publish_copy_lines)
    found_forbidden = [
        term for term in forbidden_publish_phrases if term in publish_copy
    ] + [term for term in forbidden_content_claims if term in content_section]
    if found_forbidden:
        raise RuntimeError(
            "Claude brand copy violates voice/evidence rules: "
            + "、".join(found_forbidden)
        )
    return analysis, summary


def validate_rolling_summary(summary, allowed_urls):
    summary = str(summary or "").strip()
    if len(summary) < 200 or len(summary) > 4000:
        raise RuntimeError("Claude history summary length is outside the accepted range")
    summary_urls = set(
        re.findall(r"https://www\.zhihu\.com/question/\d+(?:/answer/\d+)?", summary)
    )
    if len(summary_urls) < 3 or summary_urls - allowed_urls:
        raise RuntimeError("Claude history summary has invalid or insufficient evidence links")
    return summary


def validate_history_summary(payload, allowed_urls):
    summary = str(payload.get("history_summary_markdown") or "").strip()
    if len(summary) < 300 or len(summary) > 3000:
        raise RuntimeError("Claude bootstrap history summary length is outside the accepted range")
    used_urls = set(
        re.findall(r"https://www\.zhihu\.com/question/\d+(?:/answer/\d+)?", summary)
    )
    if len(used_urls) < 6:
        raise RuntimeError("Claude bootstrap history summary has too few evidence links")
    if used_urls - allowed_urls:
        raise RuntimeError("Claude bootstrap history summary cited unknown URLs")
    return summary


def merge_analysis(base_text, analysis, source_digest, model):
    base = source_text(base_text)
    if INSERT_BEFORE not in base:
        raise RuntimeError(f"report insertion marker missing: {INSERT_BEFORE}")
    block = (
        f"{START_PREFIX} v1 source_sha256={source_digest} model={model} -->\n\n"
        f"{analysis}\n\n{END_MARKER}\n\n"
    )
    return base.replace(INSERT_BEFORE, block + INSERT_BEFORE, 1)


def atomic_write(path, text):
    temporary = path.with_suffix(path.suffix + ".analysis.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def atomic_json(path, payload):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def main():
    args = parse_args()
    today = dt.datetime.now(ZONE)
    target_date = dt.date.fromisoformat(args.date) if args.date else today.date()
    path = report_path(target_date)
    if not path.is_file() or path.stat().st_size <= 1000:
        raise RuntimeError(f"verified source report is missing: {path}")
    original = path.read_text(encoding="utf-8")
    base = source_text(original)
    source_digest = digest_text(base)
    existing_marker = f"source_sha256={source_digest}"
    if (
        not args.force
        and START_PREFIX in original
        and existing_marker in original
        and END_MARKER in original
    ):
        print(f"already analyzed and verified by source digest: {path.name}")
        return 0

    history_days = int(os.environ.get("CLAUDE_ANALYSIS_HISTORY_DAYS", "30"))
    if history_days < 1 or history_days > 180:
        raise RuntimeError("CLAUDE_ANALYSIS_HISTORY_DAYS must be between 1 and 180")
    historical, history_text = historical_evidence(target_date, history_days)
    evidence_start = (
        historical[0][0].isoformat() if historical else target_date.isoformat()
    )
    current_text = compact_evidence(base, question_limit=30, answers_per_question=2)
    if len(report_evidence(base)) < 3 or not current_text:
        raise RuntimeError("source report has insufficient traceable evidence")
    prior_summary = read_prior_summary(target_date)
    brand_profile = BRAND_PROFILE.read_text(encoding="utf-8")
    recent_history = recent_history_excerpt(history_text)
    prompt = build_prompt(
        target_date,
        current_text,
        recent_history,
        prior_summary,
        brand_profile,
        evidence_start,
    )
    allowed_urls = set(re.findall(r"https://www\.zhihu\.com/question/\d+(?:/answer/\d+)?", prompt))
    if args.dry_run:
        print(json.dumps({
            "action": "analyze_with_claude_opus",
            "report": str(path),
            "source_sha256": source_digest,
            "current_questions": len(report_evidence(base)),
            "history_reports": len(historical),
            "history_start": historical[0][0].isoformat() if historical else None,
            "history_end": historical[-1][0].isoformat() if historical else None,
            "prompt_characters": len(prompt),
            "model": os.environ.get("CLAUDE_ANALYSIS_MODEL", MODEL_DEFAULT),
        }, ensure_ascii=False))
        return 0

    if prior_summary.startswith(("尚无历史滚动摘要", "历史滚动摘要不可用")) and history_text:
        bootstrap_prompt = build_history_prompt(target_date, history_text)
        bootstrap_urls = set(
            re.findall(
                r"https://www\.zhihu\.com/question/\d+(?:/answer/\d+)?",
                bootstrap_prompt,
            )
        )
        bootstrap_raw, _ = request_claude(
            bootstrap_prompt,
            schema=history_output_schema(),
            max_tokens_override=3000,
        )
        prior_summary = validate_history_summary(
            parse_json_object(bootstrap_raw), bootstrap_urls
        )
        prompt = build_prompt(
            target_date,
            current_text,
            recent_history,
            prior_summary,
            brand_profile,
            evidence_start,
        )
        allowed_urls = set(
            re.findall(
                r"https://www\.zhihu\.com/question/\d+(?:/answer/\d+)?", prompt
            )
        )

    raw, model = request_claude(prompt)
    payload = parse_json_object(raw)
    analysis, proposed_summary = validate_output(
        payload, allowed_urls, target_date, evidence_start
    )
    try:
        summary = validate_rolling_summary(proposed_summary, allowed_urls)
    except RuntimeError:
        summary = validate_rolling_summary(prior_summary, allowed_urls)
    enriched = merge_analysis(original, analysis, source_digest, model)
    if existing_marker not in enriched or END_MARKER not in enriched:
        raise RuntimeError("analysis merge verification failed")
    atomic_write(path, enriched)

    STATE.mkdir(parents=True, exist_ok=True)
    history_window = {
        "start": historical[0][0].isoformat() if historical else target_date.isoformat(),
        "end": target_date.isoformat(),
    }
    atomic_json(STATE / f"{target_date.isoformat()}.json", {
        "report": str(path),
        "source_sha256": source_digest,
        "enriched_sha256": digest_text(enriched),
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "history_window": history_window,
        "analyzed_at": today.isoformat(),
    })
    summary_state = STATE / "history-summary.json"
    replace_summary = True
    if summary_state.is_file():
        try:
            previous_date = dt.date.fromisoformat(json.loads(summary_state.read_text(encoding="utf-8"))["generated_for"])
            replace_summary = target_date >= previous_date
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass
    if replace_summary:
        atomic_json(summary_state, {
            "generated_for": target_date.isoformat(),
            "summary_markdown": summary,
            "source_report_sha256": source_digest,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "history_window": history_window,
            "updated_at": today.isoformat(),
        })
    print(f"analyzed with {model} and verified: {path.name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
