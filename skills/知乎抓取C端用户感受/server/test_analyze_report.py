#!/usr/bin/env python3
import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("analyze_report.py")
SPEC = importlib.util.spec_from_file_location("zhihu_analyze_report", MODULE_PATH)
ANALYZE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ANALYZE)


def sample_report(seed="1"):
    sections = []
    for category_index, category in enumerate(ANALYZE.KEYWORDS):
        blocks = [f"### {category}原始明细", ""]
        for index in range(1, 3):
            question_id = f"{seed}{category_index + 1}{index}00"
            blocks.extend(
                [
                    f"#### {index}. [{category}真实问题{index}](https://www.zhihu.com/question/{question_id})",
                    "",
                    "| 回答链接 | 作者 | 赞同数 | 评论数 | 可见短摘录 |",
                    "|---|---|---:|---:|---|",
                    f"| [回答 1](https://www.zhihu.com/question/{question_id}/answer/{question_id}9) | 用户甲 | {index} | 2 | 这是一条关于选择和使用感受的中文摘录 |",
                    "",
                ]
            )
        sections.append("\n".join(blocks))
    return "\n".join(
        [
            "# 国内家纺C端客户需求汇总",
            "",
            "## 一、今日核心判断",
            "",
            "## 二、三类产品需求观察",
            "",
            "## 三、消费者共同关注点",
            "",
            "## 四、建议优先动作",
            "",
            "## 五、原始问题与回答明细",
            "",
            *sections,
            "",
            "## 六、采集概况与数据说明",
            "",
        ]
    )


def valid_analysis(report_date, urls):
    filler = "消费者在购买时会先判断使用场景，再比较材质、体感和维护成本。"
    parts = []
    headings = (
        "## 消费者购买心理行为梳理",
        f"## {report_date.year + 1}年春夏新品产品上市建议",
        "## 梦莱儿推广渠道与内容导航",
        "## 分析口径与证据边界",
    )
    for index, heading in enumerate(headings):
        parts.extend(
            [
                heading,
                "",
                f"- 样本事实：{filler * 8} [证据](https://www.zhihu.com/question/{urls[index]})",
                f"- 分析推断：{filler * 8}",
                "- 待验证：需要用门店访谈、商品页点击和成交数据继续验证。",
                "",
            ]
        )
    parts.extend(
        [
            "### 用途一：品牌定位优化",
            "",
            "- 建议定位需要结合产品证据验证，缺少的信息均标记为需产品资料验证。",
            "",
            "### 用途二：分品类日常内容卖点",
            "",
            "#### 枕芯",
            "- 小红书：从睡姿选择切入；微信贴图：突出一个体感收益；公众号：解释高度与支撑。需产品资料验证。",
            "#### 被芯",
            "- 小红书：从季节场景切入；微信贴图：突出一个温度收益；公众号：解释填充与克重。需产品资料验证。",
            "#### 四件套",
            "- 小红书：从触感场景切入；微信贴图：突出一个面料收益；公众号：解释成分与工艺。需产品资料验证。",
            "### 品牌人格一致性自检",
            "- 释放焦虑：通过。看见她而非要求她：通过。让疲惫的人喘口气：通过。",
        ]
    )
    # The real model places these subsections inside the third major section.
    third_heading = "## 梦莱儿推广渠道与内容导航"
    boundary_heading = "## 分析口径与证据边界"
    full = "\n".join(parts)
    extra = full[full.index("### 用途一：品牌定位优化"):]
    full = full[:full.index("### 用途一：品牌定位优化")].rstrip()
    return full.replace(boundary_heading, extra + "\n\n" + boundary_heading, 1)


class AnalyzeReportTests(unittest.TestCase):
    def test_repairs_only_unescaped_quotes_inside_structured_string(self):
        raw = (
            '{"analysis_markdown":"消费者想知道"怎么选不踩坑"，这是事实。",'
            '"history_summary_markdown":"保留\\n换行",'
            '"evidence_window":{"current_date":"2026-08-25",'
            '"history_start":"2026-08-13","history_end":"2026-08-24"}}'
        )
        payload = ANALYZE.parse_json_object(raw)
        self.assertIn('"怎么选不踩坑"', payload["analysis_markdown"])
        self.assertEqual("保留\n换行", payload["history_summary_markdown"])

    def test_structured_output_schema_requires_all_report_fields(self):
        schema = ANALYZE.structured_output_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            {"analysis_markdown", "history_summary_markdown", "evidence_window"},
            set(schema["required"]),
        )
        self.assertFalse(
            schema["properties"]["evidence_window"]["additionalProperties"]
        )

    def test_recent_history_excerpt_keeps_only_latest_reports(self):
        history = "## 2026-08-20\n甲\n\n## 2026-08-21\n乙\n\n## 2026-08-22\n丙"
        excerpt = ANALYZE.recent_history_excerpt(history, limit=2)
        self.assertNotIn("2026-08-20", excerpt)
        self.assertIn("2026-08-21", excerpt)
        self.assertIn("2026-08-22", excerpt)

    def test_extracts_traceable_balanced_evidence(self):
        evidence = ANALYZE.report_evidence(sample_report())
        self.assertEqual(6, len(evidence))
        self.assertEqual({"枕芯", "被芯", "四件套"}, {row["category"] for row in evidence})
        self.assertEqual(1, len(evidence[0]["answers"]))
        self.assertGreater(evidence[0]["score"], 0)
        compact = ANALYZE.compact_evidence(sample_report(), 6, 1)
        self.assertEqual(6, compact.count("样本内可见赞同"))

    def test_source_digest_ignores_existing_analysis_block(self):
        source = sample_report()
        enriched = ANALYZE.merge_analysis(source, "## 占位分析", "abc", "opus")
        self.assertEqual(source, ANALYZE.source_text(enriched))

    def test_validates_chinese_analysis_and_rejects_unknown_link(self):
        report_date = dt.date(2026, 8, 25)
        ids = ["111", "222", "333", "444"]
        analysis = valid_analysis(report_date, ids)
        summary_links = " ".join(
            f"[历史证据](https://www.zhihu.com/question/{value})" for value in ids
        )
        payload = {
            "analysis_markdown": analysis,
            "history_summary_markdown":
                "历史趋势显示消费者持续重视体感、适配和购买风险。" * 12
                + summary_links,
            "evidence_window": {},
        }
        allowed = {f"https://www.zhihu.com/question/{value}" for value in ids}
        returned, summary = ANALYZE.validate_output(payload, allowed, report_date)
        self.assertEqual(analysis.strip(), returned)
        self.assertTrue(summary)
        payload["analysis_markdown"] = analysis.replace("question/111", "question/999")
        with self.assertRaisesRegex(RuntimeError, "outside"):
            ANALYZE.validate_output(payload, allowed, report_date)

    def test_rejects_aggressive_or_fabricated_brand_copy(self):
        report_date = dt.date(2026, 8, 25)
        ids = ["111", "222", "333", "444"]
        analysis = valid_analysis(report_date, ids).replace(
            "### 品牌人格一致性自检",
            "- 小红书标题：我试了三种材质，别被忽悠\n\n### 品牌人格一致性自检",
        )
        links = " ".join(
            f"[证据](https://www.zhihu.com/question/{value})" for value in ids
        )
        payload = {
            "analysis_markdown": analysis,
            "history_summary_markdown": "历史趋势需要持续观察。" * 20 + links,
        }
        allowed = {f"https://www.zhihu.com/question/{value}" for value in ids}
        with self.assertRaisesRegex(RuntimeError, "voice/evidence"):
            ANALYZE.validate_output(payload, allowed, report_date)

    def test_invalid_new_rolling_summary_can_be_replaced_by_valid_prior(self):
        allowed = {
            "https://www.zhihu.com/question/111",
            "https://www.zhihu.com/question/222",
            "https://www.zhihu.com/question/333",
        }
        with self.assertRaisesRegex(RuntimeError, "insufficient"):
            ANALYZE.validate_rolling_summary("没有链接的短摘要" * 30, allowed)
        valid = "历史稳定趋势需要继续观察。" * 20 + " ".join(
            f"[证据]({url})" for url in sorted(allowed)
        )
        self.assertEqual(valid, ANALYZE.validate_rolling_summary(valid, allowed))

    def test_main_dry_run_uses_current_and_historical_reports_without_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reports = root / "reports"
            state = root / "state"
            reports.mkdir()
            (reports / "0824家纺c端客户抓取.md").write_text(sample_report("2"), encoding="utf-8")
            (reports / "0825家纺c端客户抓取.md").write_text(sample_report("3"), encoding="utf-8")
            with (
                mock.patch.object(ANALYZE, "REPORTS", reports),
                mock.patch.object(ANALYZE, "STATE", state),
                mock.patch.object(ANALYZE, "parse_args", return_value=mock.Mock(date="2026-08-25", dry_run=True)),
                mock.patch("builtins.print") as output,
            ):
                self.assertEqual(0, ANALYZE.main())
            payload = json.loads(output.call_args.args[0])
            self.assertEqual(1, payload["history_reports"])
            self.assertEqual(6, payload["current_questions"])
            self.assertGreater(payload["prompt_characters"], 1000)


if __name__ == "__main__":
    unittest.main()
