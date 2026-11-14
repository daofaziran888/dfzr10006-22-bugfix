"""数据订阅推送接口：维护订阅任务，覆盖启动、暂停、继续、重试与归档动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.subscribe import SubscribeService

router = APIRouter(prefix="/api/subscribe", tags=["数据订阅推送"])

service = SubscribeService()

LIST_FIELDS = ["任务编号", "数据源名称", "推送目标", "推送格式", "推送频率", "过滤条件", "最近推送", "任务状态"]
STATUSES = ["草稿", "推送中", "已暂停", "已完成"]


@router.get("/stats", response_model=dict)
def stats() -> dict[str, int]:
    """列表页统计；与列表、详情共用同一份推送结果口径。"""
    return service.stats()


@router.get("/export")
def export_entries(
    keyword: str | None = Query(default=None, description="按任务编号检索"),
    status: str | None = Query(default=None, description="草稿、推送中、已暂停、已完成"),
    result: str | None = Query(default=None, description="未启动、推送中、已停止、可重试、已送达、已完成、空队列"),
    source: str | None = Query(default=None, description="按数据源名称检索"),
    target: str | None = Query(default=None, description="按推送目标检索"),
    任务编号: str | None = Query(default=None, include_in_schema=False),
    数据源名称: str | None = Query(default=None, include_in_schema=False),
    推送目标: str | None = Query(default=None, include_in_schema=False),
) -> dict[str, Any]:
    """导出数据订阅推送清单：沿用列表页当前的过滤条件，返回全量数据。"""
    items, total = service.list_entries(
        keyword=keyword or 任务编号,
        status=status,
        result=result,
        source=source or 数据源名称,
        target=target or 推送目标,
        page=1,
        size=10000,
    )
    return {"module": "subscribe", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按任务编号检索"),
    status: str | None = Query(default=None, description="草稿、推送中、已暂停、已完成"),
    result: str | None = Query(default=None, description="未启动、推送中、已停止、可重试、已送达、已完成"),
    source: str | None = Query(default=None, description="按数据源名称检索"),
    target: str | None = Query(default=None, description="按推送目标检索"),
    任务编号: str | None = Query(default=None, include_in_schema=False),
    数据源名称: str | None = Query(default=None, include_in_schema=False),
    推送目标: str | None = Query(default=None, include_in_schema=False),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按任务编号、来源、目标、状态与推送结果过滤列表。

    历史页面用中文字段名做过滤条件，这里同时兼容新旧两种参数名；
    没有数据时返回空页，页面展示空态并允许重试。
    """
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword or 任务编号,
        status=status,
        result=result,
        source=source or 数据源名称,
        target=target or 推送目标,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条订阅任务明细（含推送队列与最近推送记录）；不存在时给出可读说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"订阅任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条订阅任务，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="订阅任务已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """执行启动推送、暂停推送、继续推送、重试推送、归档任务。

    非法状态流转、空队列、推送目标失败都会返回 ok=False 并带上原因与恢复位置，
    同时回传任务当前明细，页面不会停留在上一轮的推送结果上。
    """
    action = str(payload.values.get("action") or "").strip()
    entry, ok, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=ok, message=message, entry=entry)
