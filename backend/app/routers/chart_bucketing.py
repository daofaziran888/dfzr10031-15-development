"""图表分桶聚合接口：数据来源、图表数据快照、复算与两期对比。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.bucketing.comparison import report_from_dict, render_report_text
from app.services.chart_bucketing import service

router = APIRouter(prefix="/api/chart-bucketing", tags=["图表分桶聚合"])


class SourceRegisterPayload(BaseModel):
    name: str
    label: str
    coordinate_field: str
    value_field: str
    records: list[dict[str, Any]] = Field(default_factory=list)
    description: str = ""


class SnapshotCreatePayload(BaseModel):
    source_name: str
    edges: list[float]
    chart_name: str
    include_underflow: bool = True
    include_overflow: bool = True


class ReplayPayload(BaseModel):
    edges: list[float]
    include_underflow: bool = True
    include_overflow: bool = True
    chart_name: str | None = None


class ComparePayload(BaseModel):
    base_snapshot_id: str
    target_snapshot_id: str
    mode: str | None = None
    as_text: bool = False


@router.get("/sources")
def list_sources() -> dict[str, Any]:
    """列出可选数据来源及其字段、行数与输入指纹。"""
    items = service.list_sources()
    return {"items": items, "total": len(items)}


@router.get("/sources/{name}")
def get_source(name: str) -> dict[str, Any]:
    try:
        summary = service.source_summary(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    summary["records"] = service.get_records(name)
    return summary


@router.post("/sources")
def register_source(payload: SourceRegisterPayload) -> dict[str, Any]:
    """登记一组内存记录作为新的数据来源。"""
    try:
        return service.register_source(
            payload.name,
            payload.label,
            payload.records,
            coordinate_field=payload.coordinate_field,
            value_field=payload.value_field,
            description=payload.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/snapshots")
def list_snapshots() -> dict[str, Any]:
    """列出全部图表数据快照（含桶行数、覆盖范围与指纹）。"""
    items = service.list_snapshots()
    return {"items": items, "total": len(items)}


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: str) -> dict[str, Any]:
    try:
        return service.get_snapshot(snapshot_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/snapshots")
def create_snapshot(payload: SnapshotCreatePayload) -> dict[str, Any]:
    """从选定数据来源读取坐标字段与数值列，按区间聚合生成快照。"""
    try:
        return service.create_snapshot(
            source_name=payload.source_name,
            edges=payload.edges,
            chart_name=payload.chart_name,
            include_underflow=payload.include_underflow,
            include_overflow=payload.include_overflow,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/snapshots/{snapshot_id}/recompute")
def recompute_snapshot(snapshot_id: str) -> dict[str, Any]:
    """用输入记录和原分桶口径复算，逐字段核对原快照能否重现。"""
    try:
        return service.recompute(snapshot_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/snapshots/{snapshot_id}/replay")
def replay_snapshot(snapshot_id: str, payload: ReplayPayload) -> dict[str, Any]:
    """用新分桶口径重放原输入记录，生成新快照；旧快照保持不变。"""
    try:
        return service.replay_snapshot(
            snapshot_id,
            edges=payload.edges,
            include_underflow=payload.include_underflow,
            include_overflow=payload.include_overflow,
            chart_name=payload.chart_name,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/compare")
def compare(payload: ComparePayload) -> dict[str, Any] | str:
    """两期分布对比：各档行数、不可直接比较区间与总行数核对。"""
    try:
        report = service.compare(
            payload.base_snapshot_id,
            payload.target_snapshot_id,
            mode=payload.mode,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.as_text:
        return render_report_text(report_from_dict(report))
    return report
