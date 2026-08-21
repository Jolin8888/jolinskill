#!/usr/bin/env python3
import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import URLError


MODULE_PATH = Path(__file__).with_name("send_dingtalk.py")
SPEC = importlib.util.spec_from_file_location("zhihu_send_dingtalk", MODULE_PATH)
SEND = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SEND)


class DingTalkSenderTests(unittest.TestCase):
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
