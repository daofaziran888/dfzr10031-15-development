"""图表分桶聚合引擎。

只依赖 Python 标准库，负责三件事：

1. ``spec``：分桶口径（连续半开区间、上下溢桶、边界归属）；
2. ``snapshot``：从数据来源的输入记录生成可重算的图表数据快照；
3. ``comparison``：两期快照的对比视图，标出可比较区间与不可直接比较的区间。

任何一版图表都能用「输入记录 + 分桶口径」复算，快照内的输入指纹与聚合结果
指纹让复算结果可以逐字段校验。
"""
from __future__ import annotations

from app.bucketing.spec import (
    UNDERFLOW_KEY,
    OVERFLOW_KEY,
    MISSING_KEY,
    BucketingSpec,
    parse_number,
)
from app.bucketing.snapshot import (
    BucketStat,
    MissingStat,
    ChartSnapshot,
    build_snapshot,
    recompute_snapshot,
    verify_snapshot,
    records_fingerprint,
)
from app.bucketing.comparison import (
    BucketComparisonRow,
    ComparisonReport,
    compare_snapshots,
    report_from_dict,
    render_report_text,
)

__all__ = [
    "UNDERFLOW_KEY",
    "OVERFLOW_KEY",
    "MISSING_KEY",
    "BucketingSpec",
    "parse_number",
    "BucketStat",
    "MissingStat",
    "ChartSnapshot",
    "build_snapshot",
    "recompute_snapshot",
    "verify_snapshot",
    "records_fingerprint",
    "BucketComparisonRow",
    "ComparisonReport",
    "compare_snapshots",
    "report_from_dict",
    "render_report_text",
]
