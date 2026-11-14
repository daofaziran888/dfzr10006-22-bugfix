"""数据订阅推送回归测试：状态机、失败恢复、并发与空队列。

运行：backend/.venv/bin/python -m unittest discover -s backend/tests -t backend
"""
from __future__ import annotations

import threading
import unittest

from app.store import store
from app.services.subscribe import SubscribeService


class SubscribeServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        self.service = SubscribeService()

    def test_status_machine_only_forward(self) -> None:
        # SUBS-0003 草稿不能直接归档，也不能暂停或重试。
        for action in ("归档任务", "暂停推送", "重试推送"):
            entry, ok, message = self.service.run_action(3, action)
            self.assertFalse(ok, action)
            self.assertEqual(entry["status"], "草稿")
            self.assertTrue(message)

        entry, ok, _ = self.service.run_action(3, "启动推送")
        self.assertTrue(ok)
        self.assertEqual(entry["status"], "推送中")

        entry, ok, _ = self.service.run_action(3, "暂停推送")
        self.assertTrue(ok)
        self.assertEqual(entry["status"], "已暂停")
        self.assertEqual(entry["推送结果"], "已停止")

        entry, ok, _ = self.service.run_action(3, "归档任务")
        self.assertTrue(ok)
        self.assertEqual(entry["status"], "已完成")

    def test_paused_task_accepts_no_delivery(self) -> None:
        entry, ok, _ = self.service.run_action(2, "暂停推送")
        self.assertTrue(ok)
        before = [target["状态"] for target in entry["推送队列"]]
        attempts = [target["尝试次数"] for target in entry["推送队列"]]

        for action in ("重试推送", "启动推送"):
            entry, ok, _ = self.service.run_action(2, action)
            self.assertFalse(ok, action)
            self.assertEqual(entry["status"], "已暂停")
            self.assertEqual(entry["推送结果"], "已停止")
        self.assertEqual([target["状态"] for target in entry["推送队列"]], before)
        self.assertEqual([target["尝试次数"] for target in entry["推送队列"]], attempts)

    def test_failure_keeps_last_push_record_and_reports_recovery(self) -> None:
        entry = self.service.get_entry(1)
        self.assertEqual(entry["推送结果"], "可重试")
        self.assertEqual(entry["最近推送"], "2026-11-14 09:10:00 / 风控中台 / 成功")
        failed = [target for target in entry["推送队列"] if target["状态"] == "失败"]
        self.assertEqual(failed[0]["名称"], "报表服务")
        self.assertIn("报表服务", failed[0]["恢复位置"])
        self.assertEqual(entry["恢复位置"], "从第 2 个目标「报表服务」继续投递")

    def test_retry_resumes_from_interrupted_target(self) -> None:
        entry, ok, message = self.service.run_action(1, "重试推送")
        self.assertTrue(ok, message)
        self.assertEqual(entry["推送结果"], "已送达")
        states = {target["名称"]: target["尝试次数"] for target in entry["推送队列"]}
        # 风控中台第一轮已成功，不在重试里重投；报表服务与审计归档从中断处继续。
        self.assertEqual(states["风控中台"], 1)
        self.assertEqual(states["报表服务"], 2)
        self.assertEqual(states["审计归档"], 1)
        self.assertIsNone(entry["恢复位置"])

    def test_failed_retry_does_not_overwrite_recent_record(self) -> None:
        entry, ok, _ = self.service.run_action(1, "重试推送")
        self.assertTrue(ok)
        record = entry["最近推送记录"]
        # 人为制造第二轮失败：最近推送记录必须仍指向最近的成功投递。
        raw = store.find("subscribe", 1)
        raw["推送队列"][1]["尝试次数"] = 0
        raw["推送队列"][1]["状态"] = "失败"
        raw["推送队列"][2]["状态"] = "中断"
        entry, ok, _ = self.service.run_action(1, "重试推送")
        self.assertFalse(ok)
        self.assertEqual(entry["最近推送记录"], record)
        self.assertEqual(entry["推送结果"], "可重试")

    def test_empty_queue_is_visible_and_retryable(self) -> None:
        entry, _ = self.service.create_entry(
            {"任务编号": "SUBS-EMPTY", "数据源名称": "空队列", "推送目标": "A、B"}
        )
        store.rows("subscribe")[-1]["推送队列"] = []
        _, ok, _ = self.service.run_action(entry["id"], "启动推送")
        self.assertTrue(ok)
        entry = self.service.get_entry(entry["id"])
        self.assertEqual(entry["推送结果"], "空队列")
        self.assertTrue(entry["可重试"])
        entry, ok, _ = self.service.run_action(entry["id"], "重试推送")
        self.assertTrue(ok)
        self.assertEqual([target["名称"] for target in entry["推送队列"]], ["A", "B"])

    def test_concurrent_pause_and_delivery_only_one_state_wins(self) -> None:
        outcomes: list[tuple[str, bool]] = []
        lock = threading.Lock()

        def worker(action: str) -> None:
            _, ok, _ = self.service.run_action(2, action)
            with lock:
                outcomes.append((action, ok))

        threads = [
            threading.Thread(target=worker, args=(action,))
            for action in ("暂停推送", "重试推送") * 100
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        entry = self.service.get_entry(2)
        self.assertEqual(entry["status"], "已暂停")
        self.assertEqual(entry["推送结果"], "已停止")
        # 暂停只成功一次，投递一次都不能成功。
        self.assertEqual(sum(1 for action, ok in outcomes if action == "暂停推送" and ok), 1)
        self.assertEqual(sum(1 for action, ok in outcomes if action == "重试推送" and ok), 0)
        self.assertEqual(
            [target["状态"] for target in entry["推送队列"]],
            ["成功", "待投递", "待投递"],
        )


if __name__ == "__main__":
    unittest.main()
