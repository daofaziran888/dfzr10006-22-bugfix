"""数据订阅推送接口回归：列表、详情、动作与导出共用同一份状态。

直接调用路由函数，避免为测试引入额外的 HTTP 客户端依赖。
运行：backend/.venv/bin/python -m unittest discover -s backend/tests -t backend
"""
from __future__ import annotations

import unittest

from app.routers.subscribe import export_entries, get_entry, list_entries, run_action
from app.schemas import EntryPayload
from app.store import store


class SubscribeApiTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()

    def action(self, entry_id: int, action: str) -> dict:
        return run_action(
            entry_id, EntryPayload(values={"action": action})
        ).model_dump()

    def test_list_and_detail_share_derived_result(self) -> None:
        page = list_entries(
            keyword=None, status=None, result=None, source=None, target=None,
            任务编号=None, 数据源名称=None, 推送目标=None,
        )
        by_code = {item["任务编号"]: item for item in page.items}
        detail = get_entry(3)
        self.assertEqual(by_code["SUBS-0003"]["推送结果"], "未启动")
        self.assertEqual(detail["推送结果"], by_code["SUBS-0003"]["推送结果"])
        self.assertEqual(detail["status"], by_code["SUBS-0003"]["status"])

    def test_draft_archive_is_rejected_with_reason(self) -> None:
        payload = self.action(3, "归档任务")
        self.assertFalse(payload["ok"])
        self.assertIn("尚未启动", payload["message"])
        self.assertEqual(payload["entry"]["推送结果"], "未启动")
        self.assertEqual(payload["entry"]["status"], "草稿")

    def test_paused_task_rejects_delivery_and_reports_stopped(self) -> None:
        self.assertTrue(self.action(2, "暂停推送")["ok"])
        for action in ("重试推送", "启动推送"):
            payload = self.action(2, action)
            self.assertFalse(payload["ok"])
            self.assertTrue(
                "暂停后不得投递" in payload["message"] or "不得新增投递" in payload["message"]
            )
            self.assertEqual(payload["entry"]["推送结果"], "已停止")

    def test_target_failure_reports_reason_and_retry_location(self) -> None:
        detail = get_entry(1)
        self.assertEqual(detail["推送结果"], "可重试")
        self.assertIn("报表服务", detail["恢复位置"])
        self.assertEqual(detail["最近推送"], "2026-11-14 09:10:00 / 风控中台 / 成功")
        failed = next(target for target in detail["推送队列"] if target["状态"] == "失败")
        self.assertIn("超时", failed["失败原因"])

        payload = self.action(1, "重试推送")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["entry"]["推送结果"], "已送达")

    def test_export_keeps_filters(self) -> None:
        exported = export_entries(
            keyword=None, status=None, result="可重试", source=None, target=None,
            任务编号=None, 数据源名称=None, 推送目标=None,
        )
        self.assertEqual(exported["total"], 1)
        self.assertEqual(exported["items"][0]["任务编号"], "SUBS-0001")


if __name__ == "__main__":
    unittest.main()
