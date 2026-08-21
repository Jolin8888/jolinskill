#!/usr/bin/env python3
import datetime as dt
import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("send_dingtalk.py")
SPEC = importlib.util.spec_from_file_location("zhihu_send_dingtalk", MODULE_PATH)
SEND = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SEND)


class DingTalkSenderTests(unittest.TestCase):
    def test_split_markdown_preserves_content_and_limit(self):
        report = "# 报告\n\n" + "\n\n".join(
            f"## 问题 {index}\n" + ("用户感受" * 80) for index in range(12)
        )
        parts = SEND.split_markdown(report, 900)
        self.assertGreater(len(parts), 1)
        self.assertLessEqual(len(parts), SEND.MAX_PARTS)
        self.assertTrue(all(len(part) <= 900 for part in parts))
        joined = "\n\n".join(parts)
        for index in range(12):
            self.assertIn(f"## 问题 {index}", joined)

    def test_decorated_part_contains_keyword_date_and_sequence(self):
        text = SEND.decorated_part("正文", dt.date(2026, 8, 20), 2, 7)
        self.assertIn("家纺报告", text)
        self.assertIn("2026-08-20", text)
        self.assertIn("第 2/7 部分", text)

    def test_rejects_too_small_chunk_limit(self):
        with self.assertRaisesRegex(RuntimeError, "at least 500"):
            SEND.split_markdown("# 报告", 499)

    def test_positive_number_env_rejects_non_positive_value(self):
        with mock.patch.dict(os.environ, {"CHUNK_TEST": "0"}):
            with self.assertRaisesRegex(RuntimeError, "must be positive"):
                SEND.positive_number_env("CHUNK_TEST", 3500, int)


if __name__ == "__main__":
    unittest.main()
