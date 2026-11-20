"""两期图表快照对比。

解决的问题：连续两期的图放在同一看板时，后一批观测可能超出旧分桶的上限，
旧桶的可比性与新数据尾部的分辨率发生冲突。本模块给出两种口径并明确标注：

- ``replay``（重放）：用统一的新分桶口径对两期输入记录各算一版，所有有限
  桶区间一致，全部可以直接比较，尾部也有完整分辨率；
- ``overflow``（保留旧桶 + 溢出区）：旧桶 ``[e0,en)`` 原样保留，``en`` 及
  以上并入开放式溢出桶；旧桶人数可按原快照复现，但溢出桶是无界区间，只能
  看溢出的整体分布（行数、最小/最大值），不能与任何有限桶直接比较分辨率。

对比结果逐桶列出两期行数、数值覆盖范围、行差，并单列「无法直接比较的区间」，
同时核对两期总行数是否各自对得上（分桶行数 + 缺失数 = 输入行数）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.bucketing.spec import OVERFLOW_KEY, BucketingSpec
from app.bucketing.snapshot import BucketStat, ChartSnapshot

MODE_REPLAY = "replay"
MODE_OVERFLOW = "overflow"

# 可比性三档：
# - comparable：两期都存在、边界完全一致的有限桶，可直接比较；
# - limited：开放式上下溢桶，行数仍可对照，但区间无界、分辨率不可比；
# - none：区间只出现在其中一期，无法对照。
COMPARABLE = "comparable"
LIMITED = "limited"
NONE = "none"


@dataclass
class BucketComparisonRow:
    """同一区间在两期快照中的对照行。"""

    key: str
    label: str
    kind: str
    open_ended: bool
    present_in_base: bool
    present_in_target: bool
    directly_comparable: bool
    comparability: str
    base_count: int | None
    target_count: int | None
    count_delta: int | None
    base_min: float | None
    base_max: float | None
    target_min: float | None
    target_max: float | None
    note: str

    @property
    def has_data(self) -> bool:
        return (self.base_count or 0) > 0 or (self.target_count or 0) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "open_ended": self.open_ended,
            "present_in_base": self.present_in_base,
            "present_in_target": self.present_in_target,
            "directly_comparable": self.directly_comparable,
            "comparability": self.comparability,
            "base_count": self.base_count,
            "target_count": self.target_count,
            "count_delta": self.count_delta,
            "base_value_range": _range(self.base_min, self.base_max),
            "target_value_range": _range(self.target_min, self.target_max),
            "note": self.note,
        }


@dataclass
class ComparisonReport:
    """两期分布对比报告。"""

    mode: str
    base_snapshot_id: str
    target_snapshot_id: str
    spec_signature: dict[str, Any]
    rows: list[BucketComparisonRow] = field(default_factory=list)
    missing: list[dict[str, Any]] = field(default_factory=list)
    reconciliation: list[dict[str, Any]] = field(default_factory=list)
    totals_match: dict[str, Any] = field(default_factory=dict)
    non_comparable_intervals: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "mode_label": MODE_LABELS[self.mode],
            "base_snapshot_id": self.base_snapshot_id,
            "target_snapshot_id": self.target_snapshot_id,
            "spec_signature": self.spec_signature,
            "rows": [row.to_dict() for row in self.rows],
            "missing": self.missing,
            "reconciliation": self.reconciliation,
            "totals_match": self.totals_match,
            "non_comparable_intervals": self.non_comparable_intervals,
            "summary": self.summary,
        }


MODE_LABELS = {
    MODE_REPLAY: "重放两期·统一新桶（全部区间可直接比较）",
    MODE_OVERFLOW: "保留旧桶·20 以上并入溢出区（旧桶可复现，溢出区间不可直接比较）",
}


def _range(minimum: float | None, maximum: float | None) -> dict[str, float] | None:
    if minimum is None or maximum is None:
        return None
    return {"min": minimum, "max": maximum}


def _spec_signature(spec: BucketingSpec) -> dict[str, Any]:
    return {
        "edges": list(spec.edges),
        "include_underflow": spec.include_underflow,
        "include_overflow": spec.include_overflow,
        "boundary_policy": spec.boundary_policy,
    }


def _index(buckets: list[BucketStat]) -> dict[str, BucketStat]:
    return {bucket.key: bucket for bucket in buckets}


def _same_interval(base: BucketStat, target: BucketStat) -> bool:
    """两个桶是否为同一区间：有限桶按标签（边界）一致，开放桶按种类一致。"""
    if base.kind != target.kind:
        return False
    if base.kind in ("underflow", "overflow"):
        return True
    return base.label == target.label


def compare_snapshots(
    base: ChartSnapshot,
    target: ChartSnapshot,
    *,
    mode: str | None = None,
) -> ComparisonReport:
    """生成两期分布对比。

    ``mode`` 不传时自动识别：口径不同，或对照期有数据落入开放式区间（新观测
    超出旧分桶覆盖范围）时按溢出视图处理；统一口径且无溢出时按重放视图处理。
    """
    if mode is None:
        # 自动识别：两期口径一致时，若对照期有数据落在开放式区间（说明新观测
        # 超出了旧分桶的覆盖范围），按「保留旧桶 + 溢出区」解读；否则说明统一
        # 口径下没有尾部溢出，按重放视图解读。口径不一致时只能按溢出口径展示
        # 那些只在单期出现的区间。
        same_spec = base.spec.to_dict() == target.spec.to_dict()
        open_bucket_rows = sum(
            bucket.row_count for bucket in target.buckets if bucket.kind in ("underflow", "overflow")
        )
        mode = MODE_OVERFLOW if (not same_spec or open_bucket_rows > 0) else MODE_REPLAY
    if mode not in MODE_LABELS:
        raise ValueError(f"未知对比模式：{mode}")

    base_index = _index(base.buckets)
    target_index = _index(target.buckets)
    ordered_keys = list(dict.fromkeys([*(bucket.key for bucket in base.buckets),
                                       *(bucket.key for bucket in target.buckets)]))

    rows: list[BucketComparisonRow] = []
    for key in ordered_keys:
        base_bucket = base_index.get(key)
        target_bucket = target_index.get(key)
        reference = base_bucket or target_bucket
        assert reference is not None
        present_base = base_bucket is not None
        present_target = target_bucket is not None
        open_ended = reference.kind in ("underflow", "overflow")
        base_count = None if base_bucket is None else base_bucket.row_count
        target_count = None if target_bucket is None else target_bucket.row_count
        delta = None if base_count is None or target_count is None else target_count - base_count
        if present_base and present_target:
            same_interval = _same_interval(base_bucket, target_bucket)  # type: ignore[arg-type]
        else:
            same_interval = False

        if not present_base or not present_target or not same_interval:
            comparability = NONE
            directly_comparable = False
        elif open_ended:
            # 开放式区间：行数仍可对照，但无界区间内没有分辨率，只能有限可比
            comparability = LIMITED
            directly_comparable = False
        else:
            comparability = COMPARABLE
            directly_comparable = True

        if comparability == NONE:
            note = "该区间仅出现在其中一期，无法直接比较"
        elif comparability == LIMITED:
            if reference.kind == "overflow":
                note = "开放式溢出区间：行数可对照，但区间无上限，内部数值分辨率不可比"
            else:
                note = "开放式下溢区间：行数可对照，但区间无下限，内部数值分辨率不可比"
        else:
            note = "区间边界完全一致，可直接比较"

        rows.append(BucketComparisonRow(
            key=key,
            label=reference.label,
            kind=reference.kind,
            open_ended=open_ended,
            present_in_base=present_base,
            present_in_target=present_target,
            directly_comparable=directly_comparable,
            comparability=comparability,
            base_count=base_count,
            target_count=target_count,
            count_delta=delta,
            base_min=None if base_bucket is None else base_bucket.value_min,
            base_max=None if base_bucket is None else base_bucket.value_max,
            target_min=None if target_bucket is None else target_bucket.value_min,
            target_max=None if target_bucket is None else target_bucket.value_max,
            note=note,
        ))

    missing = [
        {
            "snapshot_id": snapshot.snapshot_id,
            "missing_count": snapshot.missing.count,
            "missing_coordinates": snapshot.missing.coordinates,
        }
        for snapshot in (base, target)
    ]

    reconciliation = []
    for snapshot in (base, target):
        bucketed = snapshot.bucketed_rows
        accounted = bucketed + snapshot.missing.count
        reconciliation.append({
            "snapshot_id": snapshot.snapshot_id,
            "input_record_count": snapshot.input_record_count,
            "bucketed_rows": bucketed,
            "missing_rows": snapshot.missing.count,
            "accounted_rows": accounted,
            "total_rows": snapshot.total_rows,
            "balanced": accounted == snapshot.input_record_count == snapshot.total_rows,
        })

    totals_match = {
        "base_total": base.total_rows,
        "target_total": target.total_rows,
        "difference": target.total_rows - base.total_rows,
        "equal": base.total_rows == target.total_rows,
        "note": "两期总行数一致" if base.total_rows == target.total_rows else "两期总行数不一致，对比时注意样本规模差异",
    }

    # 无法直接比较的区间：仅出现在一期的区间，以及承载了实际数据的开放式区间；
    # 两期都为空的开放桶不构成对比障碍，不进清单。
    non_comparable = [
        row.to_dict()
        for row in rows
        if row.comparability == NONE or (row.comparability == LIMITED and row.has_data)
    ]
    comparable_count = sum(1 for row in rows if row.directly_comparable)
    limited_count = sum(1 for row in rows if row.comparability == LIMITED)
    summary = {
        "bucket_slots": len(rows),
        "comparable_buckets": comparable_count,
        "limited_comparable_buckets": limited_count,
        "non_comparable_buckets": len(non_comparable),
        "base_total_rows": base.total_rows,
        "target_total_rows": target.total_rows,
        "target_overflow_rows": sum(
            bucket.row_count for bucket in target.buckets if bucket.kind == "overflow"
        ),
        "base_overflow_rows": sum(
            bucket.row_count for bucket in base.buckets if bucket.kind == "overflow"
        ),
    }

    return ComparisonReport(
        mode=mode,
        base_snapshot_id=base.snapshot_id,
        target_snapshot_id=target.snapshot_id,
        spec_signature=_spec_signature(target.spec if mode == MODE_REPLAY else base.spec),
        rows=rows,
        missing=missing,
        reconciliation=reconciliation,
        totals_match=totals_match,
        non_comparable_intervals=non_comparable,
        summary=summary,
    )


def report_from_dict(payload: dict[str, Any]) -> "ComparisonReport":
    """把 ``to_dict`` 序列化后的报告还原成对象，便于重新渲染文本。"""
    rows: list[BucketComparisonRow] = []
    for item in payload["rows"]:
        base_range = item.get("base_value_range")
        target_range = item.get("target_value_range")
        rows.append(BucketComparisonRow(
            key=item["key"],
            label=item["label"],
            kind=item["kind"],
            open_ended=item["open_ended"],
            present_in_base=item["present_in_base"],
            present_in_target=item["present_in_target"],
            directly_comparable=item["directly_comparable"],
            comparability=item.get("comparability", COMPARABLE if item["directly_comparable"] else NONE),
            base_count=item["base_count"],
            target_count=item["target_count"],
            count_delta=item["count_delta"],
            base_min=None if not base_range else base_range["min"],
            base_max=None if not base_range else base_range["max"],
            target_min=None if not target_range else target_range["min"],
            target_max=None if not target_range else target_range["max"],
            note=item["note"],
        ))
    return ComparisonReport(
        mode=payload["mode"],
        base_snapshot_id=payload["base_snapshot_id"],
        target_snapshot_id=payload["target_snapshot_id"],
        spec_signature=payload["spec_signature"],
        rows=rows,
        missing=payload.get("missing", []),
        reconciliation=payload.get("reconciliation", []),
        totals_match=payload.get("totals_match", {}),
        non_comparable_intervals=payload.get("non_comparable_intervals", []),
        summary=payload.get("summary", {}),
    )


def render_report_text(report: ComparisonReport) -> str:
    """把对比报告渲染成纯文本看板，方便命令行与测试直接阅读。"""
    lines: list[str] = []
    lines.append(f"对比模式：{MODE_LABELS[report.mode]}")
    lines.append(f"基准期快照：{report.base_snapshot_id}　对照期快照：{report.target_snapshot_id}")
    signature = report.spec_signature
    edges = ", ".join(str(edge) for edge in signature["edges"])
    lines.append(f"展示口径：边界 [{edges}]，下溢桶={signature['include_underflow']}，溢出桶={signature['include_overflow']}")
    lines.append("")
    header = f"{'区间':<14}{'基准期行数':>10}{'对照期行数':>10}{'行差':>8}  可比性 / 说明"
    lines.append(header)
    lines.append("-" * 72)
    for row in report.rows:
        base_text = "—" if row.base_count is None else str(row.base_count)
        target_text = "—" if row.target_count is None else str(row.target_count)
        delta_text = "—" if row.count_delta is None else f"{row.count_delta:+d}"
        comparable = {
            COMPARABLE: "可直接比较",
            LIMITED: "有限可比（开放区间）",
            NONE: "不可直接比较",
        }[row.comparability]
        lines.append(
            f"{row.label:<14}{base_text:>10}{target_text:>10}{delta_text:>8}  {comparable}：{row.note}"
        )
        if row.base_count:
            lines.append(f"{'':<14}基准期覆盖 {_fmt_range(row.base_min, row.base_max)}")
        if row.target_count:
            lines.append(f"{'':<14}对照期覆盖 {_fmt_range(row.target_min, row.target_max)}")

    lines.append("")
    lines.append("无法直接比较的区间：")
    if report.non_comparable_intervals:
        for item in report.non_comparable_intervals:
            lines.append(f"  - {item['label']}（{item['note']}）")
    else:
        lines.append("  无，所有有限桶区间两期一致。")

    lines.append("")
    lines.append("总行数核对（分桶行数 + 缺失行数 = 输入行数）：")
    for item in report.reconciliation:
        flag = "平衡" if item["balanced"] else "不平"
        lines.append(
            f"  - {item['snapshot_id']}：输入 {item['input_record_count']} = "
            f"分桶 {item['bucketed_rows']} + 缺失 {item['missing_rows']}（{flag}）"
        )
    lines.append(f"  两期总行数是否对得上：{'是' if report.totals_match['equal'] else '否'}"
                 f"（基准 {report.totals_match['base_total']} vs 对照 "
                 f"{report.totals_match['target_total']}，差 {report.totals_match['difference']:+d}）")

    lines.append("")
    lines.append("缺失值单独统计（不进入任何数值桶）：")
    for item in report.missing:
        coordinates = "、".join(item["missing_coordinates"]) if item["missing_coordinates"] else "无"
        lines.append(f"  - {item['snapshot_id']}：{item['missing_count']} 条，坐标 {coordinates}")
    return "\n".join(lines)


def _fmt_range(minimum: float | None, maximum: float | None) -> str:
    if minimum is None or maximum is None:
        return "空桶"
    return f"[{_num(minimum)}, {_num(maximum)}]"


def _num(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)
