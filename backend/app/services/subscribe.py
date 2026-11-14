"""数据订阅推送业务规则：状态流转、推送队列、失败恢复与筛选口径都收在这里。

状态只能沿「草稿 → 推送中 → 已暂停 → 已完成」推进，不允许回退：
- 启动推送：草稿 → 推送中
- 暂停推送：推送中 → 已暂停（暂停后不再新增投递）
- 重试推送：推送中 → 推送中，只重投失败/中断目标，从中断目标继续
- 归档任务：已暂停 → 已完成（其他状态一律拒绝）

所有动作在同一把进程内锁里完成状态判断与写入，并发暂停与投递最终只会留下一个有效状态。
"""
from __future__ import annotations

import copy
import threading
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "subscribe"
REQUIRED_FIELDS = ["任务编号", "数据源名称", "推送目标"]
STATUS_ORDER = ["草稿", "推送中", "已暂停", "已完成"]

DRAFT, PUSHING, PAUSED, DONE = STATUS_ORDER
UNSTARTED, WAITING, SUCCESS = "未启动", "待投递", "成功"
FAILED, INTERRUPTED = "失败", "中断"

# 动作 -> (允许的来源状态, 目标状态)
ACTION_RULES: dict[str, tuple[tuple[str, ...], str]] = {
    "启动推送": ((DRAFT,), PUSHING),
    "暂停推送": ((PUSHING,), PAUSED),
    "重试推送": ((PUSHING,), PUSHING),
    "归档任务": ((PAUSED,), DONE),
}

# 同一订阅任务的状态判断与投递写入必须串行，避免并发动作互相覆盖。
_LOCK = threading.RLock()


class SubscribeService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        result: str | None = None,
        source: str | None = None,
        target: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with _LOCK:
            rows = [self._snapshot(entry) for entry in store.rows(MODULE)]
        if keyword:
            text = keyword.strip()
            rows = [row for row in rows if text in str(row.get("任务编号", ""))]
        if source:
            text = source.strip()
            rows = [row for row in rows if text in str(row.get("数据源名称", ""))]
        if target:
            text = target.strip()
            rows = [row for row in rows if text in self._target_text(row)]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if result:
            rows = [row for row in rows if row.get("推送结果") == result]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        with _LOCK:
            entry = store.find(MODULE, entry_id)
            return self._snapshot(entry) if entry is not None else None

    def stats(self) -> dict[str, int]:
        """列表页统计：与列表接口使用同一份任务状态，不做第二套口径。"""
        with _LOCK:
            rows = [self._snapshot(entry) for entry in store.rows(MODULE)]
        return {
            "总数": len(rows),
            "推送中": sum(1 for row in rows if row.get("status") == PUSHING),
            "已暂停": sum(1 for row in rows if row.get("status") == PAUSED),
            "可重试": sum(1 for row in rows if row.get("推送结果") == "可重试"),
        }

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        with _LOCK:
            rows = store.rows(MODULE)
            entry: dict[str, Any] = {
                "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
                "版本": 0,
                "推送轮次": 0,
                "推送记录": [],
                "最近推送记录": None,
                "最近失败": None,
                "推送目标": "",
                "推送队列": [],
            }
            entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
            entry["status"] = DRAFT
            self._init_queue(entry)
            rows.append(entry)
            return self._snapshot(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, bool, str]:
        """返回 (任务, 是否成功, 说明)。被拒绝时任务带回当前状态，便于页面继续展示。"""
        with _LOCK:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, False, f"订阅任务 {entry_id} 不存在或已归档"
            if action not in ACTION_RULES:
                return self._snapshot(entry), False, f"动作「{action}」不属于数据订阅推送可执行范围"

            allowed_from, target_status = ACTION_RULES[action]
            current = entry.get("status")
            if current not in allowed_from:
                return self._snapshot(entry), False, self._reject_message(action, current)

            self._ensure_shape(entry)
            if action == "启动推送":
                self._reset_queue(entry)
                entry["最近失败"] = None
                self._enter(entry, target_status)
                outcome = self._deliver(entry)
                return self._snapshot(entry), outcome != "failed", self._outcome_message(entry, outcome, action)

            if action == "重试推送":
                restored = False
                if not entry.get("推送队列"):
                    # 队列为空时按任务配置的推送目标重建队列，再从中断位置继续。
                    self._init_queue(entry)
                    restored = bool(entry.get("推送队列"))
                if not entry.get("推送队列"):
                    return (
                        self._snapshot(entry),
                        False,
                        "推送队列为空，没有可重试的目标；请先在任务中配置推送目标",
                    )
                if not restored and not self._retryable_targets(entry):
                    return self._snapshot(entry), False, "没有需要重试的目标，当前推送目标均已投递成功"
                self._prepare_resume(entry)
                outcome = self._deliver(entry)
                return self._snapshot(entry), outcome != "failed", self._outcome_message(entry, outcome, action)

            if action == "暂停推送":
                self._enter(entry, target_status)
                return self._snapshot(entry), True, "订阅任务已暂停，暂停后不会新增投递"

            # 归档任务：只有已暂停能进入已完成。
            self._enter(entry, target_status)
            return self._snapshot(entry), True, "订阅任务已归档"

    # ------------------------------------------------------------------ 内部
    def _reject_message(self, action: str, current: str | None) -> str:
        if action == "归档任务":
            if current == DRAFT:
                return "任务尚未启动推送，归档无效，请先启动推送并暂停后再归档"
            if current == PUSHING:
                return "任务正在推送中，不能归档，请先暂停推送"
            if current == DONE:
                return "任务已归档，不能重复归档"
        if action == "启动推送":
            if current == PUSHING:
                return "任务已在推送中，不能重复启动"
            if current == PAUSED:
                return "任务已暂停，不能重新启动；暂停后只能归档，不得新增投递"
            if current == DONE:
                return "任务已归档，不能重新启动"
        if action == "暂停推送":
            if current == DRAFT:
                return "任务尚未启动，推送未开始，无需暂停"
            if current == PAUSED:
                return "任务已停止，不能重复暂停"
            if current == DONE:
                return "任务已归档，不能暂停"
        if action == "重试推送":
            if current == DRAFT:
                return "任务尚未启动，无可重试目标，请先使用「启动推送」"
            if current == PAUSED:
                return "任务已停止，暂停后不得投递；如确认无需再推送，请归档任务"
            if current == DONE:
                return "任务已归档，不能重试推送"
        return f"当前任务状态为「{current}」，不能执行「{action}」"

    def _enter(self, entry: dict[str, Any], status: str) -> None:
        entry["status"] = status
        entry["版本"] = int(entry.get("版本", 0)) + 1

    def _deliver(self, entry: dict[str, Any]) -> str:
        """按队列顺序投递；失败只影响当前目标，后续目标标记中断。"""
        queue: list[dict[str, Any]] = entry["推送队列"]
        if not queue:
            return "empty"

        entry["推送轮次"] = int(entry.get("推送轮次", 0)) + 1
        round_no = entry["推送轮次"]
        previous_record = entry.get("最近推送记录")
        round_records: list[dict[str, Any]] = []
        started = False
        for index, target in enumerate(queue):
            if target.get("状态") == SUCCESS:
                continue
            started = True
            target["状态"] = "推送中"
            target["尝试次数"] = int(target.get("尝试次数", 0)) + 1
            target["失败原因"] = None
            target["恢复位置"] = None

            failure = self._simulate_failure(target)
            if failure:
                name = target.get("名称") or f"目标 {index + 1}"
                resume = f"从第 {index + 1} 个目标「{name}」继续投递"
                target["状态"] = FAILED
                target["失败原因"] = failure
                target["恢复位置"] = resume
                for rest in queue[index + 1:]:
                    if rest.get("状态") != SUCCESS:
                        rest["状态"] = INTERRUPTED
                        rest["失败原因"] = f"前置目标「{name}」失败，本次投递中断"
                        rest["恢复位置"] = resume
                # 失败只影响当前目标；最近推送记录保留原值，不被失败轮次覆盖。
                entry["最近推送记录"] = previous_record
                entry["最近失败"] = {
                    "时间": self._now(),
                    "目标": name,
                    "原因": failure,
                    "恢复位置": resume,
                    "轮次": round_no,
                }
                return "failed"

            target["状态"] = SUCCESS
            target["失败原因"] = None
            target["恢复位置"] = None
            record = {
                "时间": self._now(),
                "目标": target.get("名称"),
                "结果": SUCCESS,
                "轮次": round_no,
            }
            round_records.append(record)
            entry.setdefault("推送记录", []).append(record)
            entry["推送记录"] = entry["推送记录"][-20:]

        if round_records:
            entry["最近推送记录"] = round_records[-1]
        entry["最近失败"] = None
        return "success" if started else "skipped"

    def _simulate_failure(self, target: dict[str, Any]) -> str | None:
        """示例数据用「模拟结果」驱动投递结果；真实实现替换成推送通道返回值。

        标记为失败的目标只在首次尝试失败，重试时恢复成功，用来验证“从中断目标继续”。
        """
        if target.get("模拟结果") == "失败" and int(target.get("尝试次数", 0)) <= 1:
            return str(target.get("模拟失败原因") or "推送通道返回失败，原因未提供")
        return None

    def _prepare_resume(self, entry: dict[str, Any]) -> None:
        """恢复投递前把中断目标放回待投递；已成功目标不重投。"""
        for target in entry.get("推送队列", []):
            if target.get("状态") == INTERRUPTED:
                target["状态"] = WAITING
                target["失败原因"] = None
                target["恢复位置"] = None
        entry["最近失败"] = None

    def _reset_queue(self, entry: dict[str, Any]) -> None:
        for target in entry.get("推送队列", []):
            target["状态"] = UNSTARTED
            target["尝试次数"] = 0
            target["失败原因"] = None
            target["恢复位置"] = None

    def _retryable_targets(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            target
            for target in entry.get("推送队列", [])
            if target.get("状态") in (FAILED, INTERRUPTED)
        ]

    def _outcome_message(self, entry: dict[str, Any], outcome: str, action: str) -> str:
        if outcome == "empty":
            return "推送队列为空，没有可投递目标"
        if outcome == "failed":
            failure = entry.get("最近失败") or {}
            return (
                f"推送中断：目标「{failure.get('目标')}」失败，{failure.get('原因')}；"
                f"{failure.get('恢复位置')}"
            )
        if outcome == "success":
            return f"订阅任务已{action}，所有目标投递成功"
        return f"订阅任务已{action}"

    def _snapshot(self, entry: dict[str, Any]) -> dict[str, Any]:
        """在锁内刷新并返回独立副本，避免响应序列化时读到下一次动作的中间状态。"""
        return copy.deepcopy(self._refresh(entry))

    def _refresh(self, entry: dict[str, Any]) -> dict[str, Any]:
        """刷新由状态和队列派生的展示字段；列表页与详情页读同一份结果。"""
        self._ensure_shape(entry)
        status = entry.get("status")
        queue: list[dict[str, Any]] = entry.get("推送队列", [])
        failed = [target for target in queue if target.get("状态") in (FAILED, INTERRUPTED)]
        succeeded = [target for target in queue if target.get("状态") == SUCCESS]

        if status == DRAFT:
            result = "未启动"
        elif status == PAUSED:
            result = "已停止"
        elif status == DONE:
            result = "已完成"
        elif not queue:
            result = "空队列"
        elif failed:
            result = "可重试"
        elif len(succeeded) == len(queue):
            result = "已送达"
        else:
            result = "推送中"

        first_retryable = failed[0] if failed else None
        if first_retryable:
            recovery = first_retryable.get("恢复位置") or (
                f"从第 {queue.index(first_retryable) + 1} 个目标"
                f"「{first_retryable.get('名称')}」继续投递"
            )
        else:
            recovery = None

        entry["推送结果"] = result
        entry["任务状态"] = status
        entry["pending"] = status in (DRAFT, PUSHING)
        entry["abnormal"] = (bool(failed) or (not queue and status == PUSHING)) and status != DONE
        entry["目标总数"] = len(queue)
        entry["已完成目标"] = len(succeeded)
        # 空队列也要能点重试：重试会按推送目标重建队列，确实没有目标时给出原因。
        entry["可重试"] = bool(failed) or (not queue and status == PUSHING)
        entry["恢复位置"] = recovery
        record = entry.get("最近推送记录")
        entry["最近推送"] = (
            f"{record.get('时间')} / {record.get('目标')} / {record.get('结果')}"
            if record else "—"
        )
        return entry

    def _ensure_shape(self, entry: dict[str, Any]) -> None:
        entry.setdefault("推送队列", [])
        entry.setdefault("推送记录", [])
        entry.setdefault("最近推送记录", None)
        entry.setdefault("最近失败", None)
        entry.setdefault("推送轮次", 0)
        entry.setdefault("版本", 0)
        # 空队列保留为空：页面据此展示空态并提供重试入口，不能凭空补出目标。

    def _configured_targets(self, entry: dict[str, Any]) -> list[str]:
        return [
            name.strip()
            for name in str(entry.get("推送目标") or "").replace("，", "、").replace(",", "、").split("、")
            if name.strip()
        ]

    def _init_queue(self, entry: dict[str, Any]) -> None:
        """按任务配置的推送目标生成队列骨架；没有配置目标时保持空队列。"""
        names = self._configured_targets(entry)
        prefix = str(entry.get("任务编号") or entry.get("id") or "SUB")
        entry["推送队列"] = [
            {
                "id": f"{prefix}-T{index + 1}",
                "名称": name,
                "状态": UNSTARTED,
                "尝试次数": 0,
                "失败原因": None,
                "恢复位置": None,
            }
            for index, name in enumerate(names)
        ]

    def _target_text(self, entry: dict[str, Any]) -> str:
        names = [str(entry.get("推送目标") or "")]
        names.extend(str(target.get("名称") or "") for target in entry.get("推送队列", []))
        return " ".join(names)

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
