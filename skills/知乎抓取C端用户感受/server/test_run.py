#!/usr/bin/env python3
import datetime as dt
import importlib.util
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo


MODULE_PATH = Path(__file__).with_name("run.py")
SPEC = importlib.util.spec_from_file_location("zhihu_customer_voice_run", MODULE_PATH)
RUN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUN)


class FakeClient:
    def search(self, keyword, search_type, offset, limit):
        page = offset // limit
        data = []
        if page == 0:
            data.append({"object": {"type": "people", "id": "ignored"}})
        base = page * 4
        for index in range(base, min(base + 4, 12)):
            question_id = str(1000 + index)
            data.append(
                {
                    "object": {
                        "type": "answer",
                        "id": str(10000 + index),
                        "question": {
                            "id": question_id,
                            "name": f"{keyword}问题{index}",
                            "answer_count": 8,
                        },
                        "author": {"name": f"作者{index}"},
                        "voteup_count": index,
                        "comment_count": index + 1,
                        "content": "<p>" + ("真实使用感受" * 30) + "</p>",
                    }
                }
            )
        return {"data": data, "paging": {"is_end": page >= 2}}

class RunnerTests(unittest.TestCase):
    def test_chinese_date_rendering_does_not_depend_on_system_locale(self):
        self.assertEqual("2026年08月21日", RUN.chinese_date(dt.date(2026, 8, 21)))

    def test_collects_first_ten_unique_questions_and_up_to_five_answers(self):
        result = RUN.collect_keyword(FakeClient(), RUN.RateLimiter(0), "枕芯", 5)
        self.assertEqual(10, len(result["questions"]))
        self.assertEqual(3, result["pages_used"])
        self.assertTrue(all(1 <= len(q["answers"]) <= 5 for q in result["questions"]))
        self.assertTrue(
            all(len(a["excerpt"]) <= 80 for q in result["questions"] for a in q["answers"])
        )
        self.assertEqual("1", result["questions"][0]["answers"][0]["comment_count"])

    def test_report_renders_and_passes_completeness_check(self):
        client = FakeClient()
        limiter = RUN.RateLimiter(0)
        results = [RUN.collect_keyword(client, limiter, keyword, 5) for keyword in RUN.KEYWORDS]
        collected_at = dt.datetime(
            2026, 8, 20, 1, 2, 3, tzinfo=ZoneInfo("America/New_York")
        )
        report = RUN.render_report(dt.date(2026, 8, 19), collected_at, results, 5)
        self.assertIn("本报告为补跑", report)
        self.assertIn("# 国内家纺C端客户需求汇总", report)
        self.assertEqual(30, report.count("\n#### "))
        self.assertIn(
            "| 回答链接 | 作者 | 赞同数 | 评论数 | 可见短摘录 |", report
        )
        self.assertNotIn("可见回答总数：0", report)
        self.assertIn("## 一、今日核心判断", report)
        self.assertIn("## 四、建议优先动作", report)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "0819家纺C端客户抓取.md"
            path.write_text(report, encoding="utf-8")
            complete, reason = RUN.validate_complete_report(path)
            self.assertTrue(complete, reason)


if __name__ == "__main__":
    unittest.main()
