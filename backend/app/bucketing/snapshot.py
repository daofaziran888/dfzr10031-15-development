"""图表数据快照：从来源记录分桶聚合，并可按原口径逐字段复算。

快照保留：

- 分桶口径（边界、上下溢开关、边界归属策略）；
- 每个桶的行数与数值覆盖范围（桶内最小值/最大值），空桶保留 0 行；
- 缺失值单独统计（数量与对应坐标，坐标用于审计而不参与聚合）；
- 输入记录指纹与聚合结果指纹：输入指纹保证复算用的是同一批记录，
  结果指纹保证复算出来的分布与原快照完全一致。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from app.bucketing.spec import MISSING_KEY, BucketingSpec, parse_number


def _canonical_json(payload: Any) -> str:
    """稳定序列化：键排序、不转义中文，保证指纹跨进程可复现。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def records_fingerprint(records: list[dict[str, Any]], coordinate_field: str, value_field: str) -> str:
    """输入记录指纹：只纳入分桶实际读取的两个字段。

    记录顺序也纳入指纹（逐条哈希），避免同一批数据被重排后指纹不变却难以察觉。
    """
    digest = hashlib.sha256()
    for record in records:
        pair = [
            None if coordinate_field not in record else record.get(coordinate_field),
            None if value_field not in record else record.get(value_field),
        ]
        digest.update(_canonical_json(pair).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _result_fingerprint(spec: BucketingSpec, buckets: list[dict[str, Any]], missing: dict[str, Any]) -> str:
    payload = {"spec": spec.to_dict(), "buckets": buckets, "missing": missing}
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass
class BucketStat:
    """单个桶的聚合结果。"""

    key: str
    label: str
    kind: str  # finite / underflow / overflow
    row_count: int
    value_min: float | None
    value_max: float | None

    def covers(self, value: float) -> bool:
        """判断数值是否落在本桶记录过的覆盖范围内（含端点）。"""
        if self.row_count == 0 or self.value_min is None or self.value_max is None:
            return False
        return self.value_min <= value <= self.value_max

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BucketStat":
        return cls(**payload)


@dataclass
class MissingStat:
    """缺失值单独统计：只数数量并保留坐标用于审计，不进任何数值桶。"""

    count: int = 0
    coordinates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MissingStat":
        return cls(count=int(payload.get("count", 0)), coordinates=list(payload.get("coordinates", [])))


@dataclass
class ChartSnapshot:
    """一版图表的数据快照（不可变的分桶聚合结果）。"""

    snapshot_id: str
    chart_name: str
    source_name: str
    coordinate_field: str
    value_field: str
    spec: BucketingSpec
    buckets: list[BucketStat]
    missing: MissingStat
    total_rows: int
    input_record_count: int
    input_fingerprint: str
    result_fingerprint: str
    created_at: str

    @property
    def bucketed_rows(self) -> int:
        return sum(bucket.row_count for bucket in self.buckets)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "chart_name": self.chart_name,
            "source_name": self.source_name,
            "coordinate_field": self.coordinate_field,
            "value_field": self.value_field,
            "spec": self.spec.to_dict(),
            "buckets": [bucket.to_dict() for bucket in self.buckets],
            "missing": self.missing.to_dict(),
            "total_rows": self.total_rows,
            "input_record_count": self.input_record_count,
            "input_fingerprint": self.input_fingerprint,
            "result_fingerprint": self.result_fingerprint,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChartSnapshot":
        return cls(
            snapshot_id=str(payload["snapshot_id"]),
            chart_name=str(payload["chart_name"]),
            source_name=str(payload["source_name"]),
            coordinate_field=str(payload["coordinate_field"]),
            value_field=str(payload["value_field"]),
            spec=BucketingSpec.from_dict(payload["spec"]),
            buckets=[BucketStat.from_dict(item) for item in payload["buckets"]],
            missing=MissingStat.from_dict(payload["missing"]),
            total_rows=int(payload["total_rows"]),
            input_record_count=int(payload["input_record_count"]),
            input_fingerprint=str(payload["input_fingerprint"]),
            result_fingerprint=str(payload["result_fingerprint"]),
            created_at=str(payload["created_at"]),
        )


def build_snapshot(
    *,
    records: list[dict[str, Any]],
    spec: BucketingSpec,
    chart_name: str,
    source_name: str,
    coordinate_field: str,
    value_field: str,
    snapshot_id: str,
    created_at: str,
) -> ChartSnapshot:
    """按口径聚合输入记录，生成图表数据快照。

    每条记录只会落到一个桶（或计入缺失）；桶统计保留行数与数值覆盖范围。
    """
    if records and coordinate_field not in records[0]:
        raise ValueError(f"来源记录缺少坐标字段：{coordinate_field}")
    if records and value_field not in records[0]:
        raise ValueError(f"来源记录缺少数值字段：{value_field}")

    definitions = spec.bucket_defs()
    counts = {definition["key"]: 0 for definition in definitions}
    minima: dict[str, float] = {}
    maxima: dict[str, float] = {}
    missing_coordinates: list[str] = []

    for record in records:
        coordinate = record.get(coordinate_field)
        try:
            value = parse_number(record.get(value_field))
        except ValueError:
            missing_coordinates.append(str(coordinate))
            continue
        key = spec.assign(value)  # 口径不接受的数值直接抛错，不静默丢数据
        counts[key] += 1
        minima[key] = value if key not in minima else min(minima[key], value)
        maxima[key] = value if key not in maxima else max(maxima[key], value)

    bucket_stats: list[BucketStat] = []
    for definition in definitions:
        key = str(definition["key"])
        bucket_stats.append(BucketStat(
            key=key,
            label=str(definition["label"]),
            kind=str(definition["kind"]),
            row_count=counts[key],
            value_min=minima.get(key),
            value_max=maxima.get(key),
        ))

    missing = MissingStat(count=len(missing_coordinates), coordinates=missing_coordinates)
    total_rows = sum(bucket.row_count for bucket in bucket_stats) + missing.count
    input_fp = records_fingerprint(records, coordinate_field, value_field)
    result_fp = _result_fingerprint(
        spec,
        [bucket.to_dict() for bucket in bucket_stats],
        missing.to_dict(),
    )
    return ChartSnapshot(
        snapshot_id=snapshot_id,
        chart_name=chart_name,
        source_name=source_name,
        coordinate_field=coordinate_field,
        value_field=value_field,
        spec=spec,
        buckets=bucket_stats,
        missing=missing,
        total_rows=total_rows,
        input_record_count=len(records),
        input_fingerprint=input_fp,
        result_fingerprint=result_fp,
        created_at=created_at,
    )


def recompute_snapshot(
    snapshot: ChartSnapshot,
    records: list[dict[str, Any]],
) -> ChartSnapshot:
    """用输入记录和原分桶口径复算一版快照，保留原快照编号与时间。

    输入指纹不一致时直接拒绝复算，避免拿另一批数据冒充原口径结果。
    """
    input_fp = records_fingerprint(records, snapshot.coordinate_field, snapshot.value_field)
    if input_fp != snapshot.input_fingerprint:
        raise ValueError("输入记录指纹与快照不一致，无法按原快照复算")
    rebuilt = build_snapshot(
        records=records,
        spec=snapshot.spec,
        chart_name=snapshot.chart_name,
        source_name=snapshot.source_name,
        coordinate_field=snapshot.coordinate_field,
        value_field=snapshot.value_field,
        snapshot_id=snapshot.snapshot_id,
        created_at=snapshot.created_at,
    )
    return rebuilt


def verify_snapshot(snapshot: ChartSnapshot, records: list[dict[str, Any]]) -> dict[str, Any]:
    """逐字段核对原快照能否由输入记录复现，返回核对明细。"""
    problems: list[str] = []
    input_fp = records_fingerprint(records, snapshot.coordinate_field, snapshot.value_field)
    if input_fp != snapshot.input_fingerprint:
        problems.append("输入记录指纹不一致")
    try:
        rebuilt = recompute_snapshot(snapshot, records)
    except ValueError as exc:
        problems.append(str(exc))
        rebuilt = None
    if rebuilt is not None:
        if rebuilt.result_fingerprint != snapshot.result_fingerprint:
            problems.append("聚合结果指纹不一致")
        if rebuilt.to_dict()["buckets"] != snapshot.to_dict()["buckets"]:
            problems.append("各桶行数或数值覆盖范围不一致")
        if rebuilt.missing.to_dict() != snapshot.missing.to_dict():
            problems.append("缺失值统计不一致")
        if rebuilt.total_rows != snapshot.total_rows:
            problems.append("总行数不一致")
    return {
        "ok": not problems,
        "snapshot_id": snapshot.snapshot_id,
        "input_fingerprint_match": input_fp == snapshot.input_fingerprint,
        "result_fingerprint_match": rebuilt is not None and rebuilt.result_fingerprint == snapshot.result_fingerprint,
        "problems": problems,
    }
