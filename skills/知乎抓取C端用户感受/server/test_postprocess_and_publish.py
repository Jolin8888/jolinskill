#!/usr/bin/env python3
import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("postprocess_and_publish.py")
SPEC = importlib.util.spec_from_file_location("zhihu_postprocess", MODULE_PATH)
PIPELINE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PIPELINE)


class PostprocessTests(unittest.TestCase):
    def test_analysis_must_finish_before_sender(self):
        calls = []

        def run(command, check):
            calls.append(Path(command[-1]).name)
            return mock.Mock(returncode=0)

        with mock.patch("subprocess.run", side_effect=run):
            self.assertEqual(0, PIPELINE.main())
        self.assertEqual(["analyze_report.py", "send_dingtalk.py"], calls)

    def test_sender_is_not_called_when_analysis_fails(self):
        with mock.patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["analyze_report.py"]),
        ) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                PIPELINE.main()
        self.assertEqual(1, run.call_count)


if __name__ == "__main__":
    unittest.main()
