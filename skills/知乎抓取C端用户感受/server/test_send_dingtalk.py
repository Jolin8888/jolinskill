#!/usr/bin/env python3
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from urllib.error import URLError


MODULE_PATH = Path(__file__).with_name("send_dingtalk.py")
SPEC = importlib.util.spec_from_file_location("zhihu_send_dingtalk", MODULE_PATH)
SEND = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SEND)


class DingTalkSenderTests(unittest.TestCase):
    def test_dry_run_uses_source_specific_document_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "0821家纺c端客户抓取.md"
            report.write_text("完整报告", encoding="utf-8")
            output = io.StringIO()
            with (
                mock.patch.object(
                    SEND,
                    "parse_args",
                    return_value=mock.Mock(date="2026-08-21", dry_run=True),
                ),
                mock.patch.object(SEND, "report_for", return_value=(report, "完整报告")),
                mock.patch.object(SEND, "STATE", root / "state"),
                mock.patch.object(SEND, "DWS", Path("/usr/bin/true")),
                mock.patch.dict(
                    "os.environ",
                    {
                        "DINGTALK_PROFILE": "profile",
                        "DINGTALK_GROUP_ID": "group",
                        "N8N_DINGTALK_WEBHOOK_URL": "http://127.0.0.1:5678/webhook/test",
                    },
                    clear=True,
                ),
                redirect_stdout(output),
            ):
                self.assertEqual(0, SEND.main())
            payload = json.loads(output.getvalue())
            self.assertEqual(
                "知乎｜国内家纺C端客户需求汇总｜2026-08-21",
                payload["document_name"],
            )

    def test_run_dws_accepts_composite_json_without_top_level_success(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"doc_results": {"success": True, "documents": []}}),
            stderr="",
        )
        with mock.patch("subprocess.run", return_value=completed):
            result = SEND.run_dws(["drive", "search"], "profile")
        self.assertIn("doc_results", result)

    def test_run_dws_rejects_structured_business_error(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"error": {"message": "approval required"}}),
            stderr="",
        )
        with mock.patch("subprocess.run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "approval required"):
                SEND.run_dws(["drive", "publish", "set"], "profile")

    def test_extracts_nested_document_node(self):
        created = {"data": {"nodeId": "abc-123", "url": "https://example.test"}}
        self.assertEqual(SEND.document_node(created), "abc-123")

    def test_exact_document_deduplicates_drive_and_doc_results(self):
        item = {"name": "日报", "nodeId": "abc"}
        result = {"doc_results": {"documents": [item]}, "drive_results": {"items": [item]}}
        node, found = SEND.exact_document(result, "日报")
        self.assertEqual(node, "abc")
        self.assertEqual(found["name"], "日报")

    def test_exact_document_rejects_multiple_nodes(self):
        result = {
            "items": [
                {"name": "日报", "nodeId": "abc"},
                {"name": "日报", "nodeId": "def"},
            ]
        }
        with self.assertRaisesRegex(RuntimeError, "multiple"):
            SEND.exact_document(result, "日报")

    def test_recovered_document_must_match_zhihu_report(self):
        name = "知乎｜国内家纺C端客户需求汇总｜2026-08-21"
        search_result = {"items": [{"name": name, "nodeId": "wrong-node"}]}
        source = (
            "## 一、今日核心判断\n## 二、三类产品需求观察\n"
            "## 三、消费者共同关注点\n## 四、建议优先动作\n"
            "## 五、原始问题与回答明细\n## 六、采集概况与数据说明\n"
            "https://www.zhihu.com/question/123"
        )
        with (
            mock.patch.object(
                SEND,
                "run_dws",
                return_value={"content": "# 家纺外贸 Reddit 用户需求日报"},
            ),
            self.assertRaisesRegex(RuntimeError, "name collision"),
        ):
            SEND.recover_verified_document(search_result, name, source, "profile")

    def test_document_url_prefers_dingtalk_url(self):
        result = {
            "url": "https://example.test",
            "data": {"shareUrl": "https://alidocs.dingtalk.com/i/nodes/abc"},
        }
        self.assertEqual(
            SEND.document_url(result, "fallback"),
            "https://alidocs.dingtalk.com/i/nodes/abc",
        )

    def test_document_url_has_safe_fallback(self):
        self.assertEqual(
            SEND.document_url({}, "abc"),
            "https://alidocs.dingtalk.com/i/nodes/abc",
        )

    def test_permission_user_ids_requires_read_capable_role(self):
        result = {
            "members": [
                {"id": "reader", "role": "READER"},
                {"userId": "editor", "role": "EDITOR"},
                {"userId": "unknown", "role": "NONE"},
            ]
        }
        self.assertEqual(SEND.permission_user_ids(result), {"reader", "editor"})

    def test_group_read_access_grants_one_user_per_request(self):
        responses = iter(
            [
                {"userId": "owner"},
                {"members": [{"id": "owner", "role": "OWNER"}]},
                {},
                {},
                {
                    "members": [
                        {"id": "owner", "role": "OWNER"},
                        {"id": "reader-1", "role": "READER"},
                        {"id": "reader-2", "role": "READER"},
                    ]
                },
            ]
        )
        with (
            mock.patch.object(
                SEND,
                "group_member_user_ids",
                return_value=["owner", "reader-1", "reader-2"],
            ),
            mock.patch.object(SEND, "run_dws", side_effect=lambda *args, **kwargs: next(responses)) as run,
        ):
            self.assertEqual(3, SEND.grant_group_read_access("node", "profile", "group"))
        add_calls = [
            call.args[0]
            for call in run.call_args_list
            if call.args[0][:3] == ["drive", "permission", "add"]
        ]
        self.assertEqual(2, len(add_calls))
        self.assertEqual("reader-1", add_calls[0][add_calls[0].index("--users") + 1])
        self.assertEqual("reader-2", add_calls[1][add_calls[1].index("--users") + 1])

    def test_n8n_webhook_must_be_local(self):
        valid = "http://127.0.0.1:5678/webhook/zhihu-market-intelligence"
        self.assertEqual(SEND.validate_n8n_webhook_url(valid), valid)
        with self.assertRaisesRegex(RuntimeError, "local HTTP loopback"):
            SEND.validate_n8n_webhook_url("https://example.com/webhook/test")

    def test_n8n_delivery_requires_matching_success(self):
        payload = {
            "title": "日报",
            "document_url": "https://alidocs.dingtalk.com/i/nodes/abc",
            "report_date": "2026-08-21",
            "idempotency_key": "key-1",
        }

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(
                    {"ok": True, "duplicate": False, "idempotency_key": "key-1"}
                ).encode()

        with mock.patch("urllib.request.urlopen", return_value=Response()):
            result = SEND.deliver_through_n8n("http://127.0.0.1:5678/webhook/x", payload)
        self.assertTrue(result["ok"])

        with mock.patch("urllib.request.urlopen", side_effect=URLError("offline")):
            with self.assertRaisesRegex(RuntimeError, "offline"):
                SEND.deliver_through_n8n("http://127.0.0.1:5678/webhook/x", payload)

    def test_readback_requires_all_question_links(self):
        source = (
            "## 一、今日核心判断\n## 二、三类产品需求观察\n"
            "## 三、消费者共同关注点\n## 四、建议优先动作\n"
            "## 五、原始问题与回答明细\n## 六、采集概况与数据说明\n"
            "https://www.zhihu.com/question/123\n"
            "https://www.zhihu.com/question/456"
        )
        SEND.validate_readback(source, source)
        with self.assertRaisesRegex(RuntimeError, "lost question links"):
            SEND.validate_readback(source, source.replace("question/456", "answer/456"))


if __name__ == "__main__":
    unittest.main()
