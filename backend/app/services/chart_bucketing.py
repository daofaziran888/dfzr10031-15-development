"""图表分桶聚合应用服务。

在纯计算引擎（:mod:`app.bucketing`）之上提供：

- 数据来源注册表：内置两期对照 CSV 与缺失值示例，也接受登记的内存记录；
- 快照仓库：生成、读取、复算、重放（用新口径重算同一批输入，旧快照保持不动）；
- 两期对比：按重放或溢出口径生成对比报告。

仓库仍是进程内存储，与平台其他模块一致；快照字典化保存，模拟真实落库后的
序列化形态，保证「存下来再读出来」也能复算。
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.bucketing.comparison import MODE_OVERFLOW, MODE_REPLAY, ComparisonReport, compare_snapshots
from app.bucketing.snapshot import (
    ChartSnapshot,
    build_snapshot,
    records_fingerprint,
    recompute_snapshot,
    verify_snapshot,
)
from app.bucketing.spec import BucketingSpec

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


class ChartBucketingService:
    """数据来源、快照与对比报告的进程内服务。"""

    def __init__(self) -> None:
        self._sources: dict[str, dict[str, Any]] = {}
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._sequence = 0
        self._seed_sources()

    # ------------------------------------------------------------------ 数据来源
    def _seed_sources(self) -> None:
        self.register_source(
            "period_1",
            "第一期观测（5、19）",
            _read_csv(DATA_DIR / "period_1.csv"),
            coordinate_field="观测编号",
            value_field="观测值",
            description="题目对照用：旧桶 [0,10)、[10,20) 下各 1 行",
        )
        self.register_source(
            "period_2",
            "第二期观测（5、20、35）",
            _read_csv(DATA_DIR / "period_2.csv"),
            coordinate_field="观测编号",
            value_field="观测值",
            description="题目对照用：20 与 35 超出旧桶上限 20",
        )
        self.register_source(
            "demo_missing",
            "缺失值与溢出示例",
            _read_csv(DATA_DIR / "demo_missing.csv"),
            coordinate_field="观测编号",
            value_field="观测值",
            description="含两条空数值记录，缺失值单独统计",
        )

    def register_source(
        self,
        name: str,
        label: str,
        records: list[dict[str, Any]],
        *,
        coordinate_field: str,
        value_field: str,
        description: str = "",
    ) -> dict[str, Any]:
        if not records:
            raise ValueError("数据来源至少需要一条记录")
        if coordinate_field not in records[0] or value_field not in records[0]:
            raise ValueError(f"记录必须同时包含坐标字段 {coordinate_field} 与数值字段 {value_field}")
        payload = {
            "name": name,
            "label": label,
            "description": description,
            "coordinate_field": coordinate_field,
            "value_field": value_field,
            "record_count": len(records),
            "records": [dict(row) for row in records],
            "input_fingerprint": records_fingerprint(records, coordinate_field, value_field),
        }
        self._sources[name] = payload
        return self.source_summary(name)

    def list_sources(self) -> list[dict[str, Any]]:
        return [self.source_summary(name) for name in sorted(self._sources)]

    def source_summary(self, name: str) -> dict[str, Any]:
        source = self._require_source(name)
        return {key: value for key, value in source.items() if key != "records"}

    def get_records(self, name: str) -> list[dict[str, Any]]:
        source = self._require_source(name)
        return [dict(row) for row in source["records"]]

    def _require_source(self, name: str) -> dict[str, Any]:
        if name not in self._sources:
            raise KeyError(f"数据来源 {name} 不存在")
        return self._sources[name]

    # ------------------------------------------------------------------ 快照
    def _next_snapshot_id(self) -> str:
        # 种子快照可能手工占用了 SNAP-B000x 编号，自增前先扫描现有编号，避免撞号覆盖
        used = set(self._snapshots)
        while True:
            self._sequence += 1
            candidate = f"SNAP-B{self._sequence:04d}"
            if candidate not in used:
                return candidate

    @staticmethod
    def _build_spec(payload: dict[str, Any]) -> BucketingSpec:
        return BucketingSpec.from_edges(
            payload["edges"],
            include_underflow=bool(payload.get("include_underflow", True)),
            include_overflow=bool(payload.get("include_overflow", True)),
        )

    def create_snapshot(
        self,
        *,
        source_name: str,
        edges: list[float],
        chart_name: str,
        include_underflow: bool = True,
        include_overflow: bool = True,
        snapshot_id: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        source = self._require_source(source_name)
        spec = self._build_spec({
            "edges": edges,
            "include_underflow": include_underflow,
            "include_overflow": include_overflow,
        })
        snapshot_id = snapshot_id or self._next_snapshot_id()
        created_at = created_at or datetime.now(timezone.utc).isoformat()
        snapshot = build_snapshot(
            records=source["records"],
            spec=spec,
            chart_name=chart_name,
            source_name=source_name,
            coordinate_field=source["coordinate_field"],
            value_field=source["value_field"],
            snapshot_id=snapshot_id,
            created_at=created_at,
        )
        return self._save(snapshot)

    def replay_snapshot(
        self,
        snapshot_id: str,
        *,
        edges: list[float],
        include_underflow: bool = True,
        include_overflow: bool = True,
        chart_name: str | None = None,
    ) -> dict[str, Any]:
        """用新分桶口径对原输入记录重算，生成一版新快照，旧快照保持不变。"""
        original = ChartSnapshot.from_dict(self._require_snapshot(snapshot_id))
        source = self._require_source(original.source_name)
        spec = self._build_spec({
            "edges": edges,
            "include_underflow": include_underflow,
            "include_overflow": include_overflow,
        })
        snapshot = build_snapshot(
            records=source["records"],
            spec=spec,
            chart_name=chart_name or f"{original.chart_name}（新口径重放）",
            source_name=original.source_name,
            coordinate_field=original.coordinate_field,
            value_field=original.value_field,
            snapshot_id=self._next_snapshot_id(),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        return self._save(snapshot)

    def _save(self, snapshot: ChartSnapshot) -> dict[str, Any]:
        self._snapshots[snapshot.snapshot_id] = snapshot.to_dict()
        return self.get_snapshot(snapshot.snapshot_id)

    def list_snapshots(self) -> list[dict[str, Any]]:
        return [self.get_snapshot(snapshot_id) for snapshot_id in sorted(self._snapshots)]

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        payload = self._require_snapshot(snapshot_id)
        snapshot = ChartSnapshot.from_dict(payload)
        data = snapshot.to_dict()
        data["bucketed_rows"] = snapshot.bucketed_rows
        return data

    def _require_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        if snapshot_id not in self._snapshots:
            raise KeyError(f"快照 {snapshot_id} 不存在")
        return self._snapshots[snapshot_id]

    def recompute(self, snapshot_id: str) -> dict[str, Any]:
        """按原快照的输入记录与分桶口径复算，并与存储的快照逐字段核对。"""
        stored = ChartSnapshot.from_dict(self._require_snapshot(snapshot_id))
        records = self.get_records(stored.source_name)
        rebuilt = recompute_snapshot(stored, records)
        verification = verify_snapshot(stored, records)
        return {
            "ok": verification["ok"],
            "snapshot_id": snapshot_id,
            "verification": verification,
            "rebuilt_result_fingerprint": rebuilt.result_fingerprint,
            "stored_result_fingerprint": stored.result_fingerprint,
        }

    def compare(
        self,
        base_snapshot_id: str,
        target_snapshot_id: str,
        *,
        mode: str | None = None,
    ) -> dict[str, Any]:
        if mode is not None and mode not in (MODE_REPLAY, MODE_OVERFLOW):
            raise ValueError("对比模式只支持 replay（重放）或 overflow（溢出）")
        base = ChartSnapshot.from_dict(self._require_snapshot(base_snapshot_id))
        target = ChartSnapshot.from_dict(self._require_snapshot(target_snapshot_id))
        report: ComparisonReport = compare_snapshots(base, target, mode=mode)
        return report.to_dict()

    def seed_demo_snapshots(self) -> dict[str, str]:
        """登记题目要求的两期快照：旧桶版、旧桶+溢出版、统一新桶重放版。"""
        old_edges = [0, 10, 20]
        new_edges = [0, 10, 20, 30, 40]
        ids: dict[str, str] = {}
        if "SNAP-B0001" not in self._snapshots:
            ids["base_old_buckets"] = self.create_snapshot(
                source_name="period_1",
                edges=old_edges,
                chart_name="第一期分布图·旧桶口径",
                include_overflow=True,
                snapshot_id="SNAP-B0001",
                created_at="2026-10-01T00:00:00+00:00",
            )["snapshot_id"]
        if "SNAP-B0002" not in self._snapshots:
            ids["target_old_buckets_overflow"] = self.create_snapshot(
                source_name="period_2",
                edges=old_edges,
                chart_name="第二期分布图·旧桶口径（含溢出区）",
                include_overflow=True,
                snapshot_id="SNAP-B0002",
                created_at="2026-11-01T00:00:00+00:00",
            )["snapshot_id"]
        if "SNAP-B0003" not in self._snapshots:
            ids["base_replay"] = self.create_snapshot(
                source_name="period_1",
                edges=new_edges,
                chart_name="第一期分布图·统一新桶重放",
                include_overflow=True,
                snapshot_id="SNAP-B0003",
                created_at="2026-10-01T00:00:00+00:00",
            )["snapshot_id"]
        if "SNAP-B0004" not in self._snapshots:
            ids["target_replay"] = self.create_snapshot(
                source_name="period_2",
                edges=new_edges,
                chart_name="第二期分布图·统一新桶重放",
                include_overflow=True,
                snapshot_id="SNAP-B0004",
                created_at="2026-11-01T00:00:00+00:00",
            )["snapshot_id"]
        if "SNAP-B0005" not in self._snapshots:
            ids["missing_demo"] = self.create_snapshot(
                source_name="demo_missing",
                edges=old_edges,
                chart_name="缺失值与溢出示例图",
                include_overflow=True,
                snapshot_id="SNAP-B0005",
                created_at="2026-11-10T00:00:00+00:00",
            )["snapshot_id"]
        return ids


service = ChartBucketingService()
service.seed_demo_snapshots()
