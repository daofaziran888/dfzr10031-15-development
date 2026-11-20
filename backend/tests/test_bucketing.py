"""分桶聚合引擎测试：题目给定数值与所有口径不变量。

直接用标准库运行：
    cd backend && python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest

from app.bucketing import (
    OVERFLOW_KEY,
    BucketingSpec,
    ChartSnapshot,
    build_snapshot,
    compare_snapshots,
    parse_number,
    records_fingerprint,
    recompute_snapshot,
    render_report_text,
    report_from_dict,
    verify_snapshot,
)
from app.bucketing.comparison import MODE_OVERFLOW, MODE_REPLAY

PERIOD_1 = [{"id": "P1-001", "v": 5}, {"id": "P1-002", "v": 19}]
PERIOD_2 = [{"id": "P2-001", "v": 5}, {"id": "P2-002", "v": 20}, {"id": "P2-003", "v": 35}]
OLD_EDGES = [0, 10, 20]
NEW_EDGES = [0, 10, 20, 30, 40]


def make(records, edges, snap_id, **kwargs):
    return build_snapshot(
        records=records,
        spec=BucketingSpec.from_edges(edges, **kwargs),
        chart_name="测试图",
        source_name="内存来源",
        coordinate_field="id",
        value_field="v",
        snapshot_id=snap_id,
        created_at="2026-11-01T00:00:00+00:00",
    )


def counts(snapshot: ChartSnapshot) -> dict[str, int]:
    return {bucket.key: bucket.row_count for bucket in snapshot.buckets}


class SpecTests(unittest.TestCase):
    def test_boundary_goes_to_left_closed_bucket(self):
        spec = BucketingSpec.from_edges(OLD_EDGES)
        # 左闭右开：10 属于 [10,20)，20 属于溢出桶，0 属于 [0,10)
        self.assertEqual(spec.assign(0), "bucket_0")
        self.assertEqual(spec.assign(9.999), "bucket_0")
        self.assertEqual(spec.assign(10), "bucket_1")
        self.assertEqual(spec.assign(19.999), "bucket_1")
        self.assertEqual(spec.assign(20), OVERFLOW_KEY)
        self.assertEqual(spec.assign(35), OVERFLOW_KEY)

    def test_intervals_are_continuous_and_partition_is_unique(self):
        spec = BucketingSpec.from_edges([0, 10, 20])
        labels = [item["label"] for item in spec.bucket_defs() if item["kind"] == "finite"]
        self.assertEqual(labels, ["[0, 10)", "[10, 20)"])  # 前桶上界即后桶下界
        # 每个边界值只会落到一个桶
        for value in [0, 10, 20]:
            self.assertEqual(len([1 for _ in [spec.assign(value)]]), 1)

    def test_invalid_edges_rejected(self):
        with self.assertRaises(ValueError):
            BucketingSpec.from_edges([10])  # 至少两个边界
        with self.assertRaises(ValueError):
            BucketingSpec.from_edges([20, 10])  # 必须递增
        with self.assertRaises(ValueError):
            BucketingSpec.from_edges([10, 10])  # 不得重复

    def test_parse_number_missing_rules(self):
        self.assertEqual(parse_number("5"), 5.0)
        for raw in (None, "", "  "):
            with self.assertRaises(ValueError):
                parse_number(raw)
        with self.assertRaises(ValueError):
            parse_number(True)  # 布尔不参与数值分桶
        with self.assertRaises(ValueError):
            parse_number("abc")

    def test_overflow_can_be_disallowed(self):
        spec = BucketingSpec.from_edges(OLD_EDGES, include_overflow=False)
        with self.assertRaises(ValueError):
            spec.assign(20)

    def test_boundary_rows_counted_in_exactly_one_bucket(self):
        # 含边界值 10、20 的记录：每条只能落一个桶，总行数守恒
        records = [{"id": f"r{i}", "v": value} for i, value in enumerate([0, 10, 19, 20, 20, 35])]
        snapshot = make(records, OLD_EDGES, "SB")
        self.assertEqual(snapshot.bucketed_rows, 6)
        by_key = {bucket.key: bucket.row_count for bucket in snapshot.buckets}
        self.assertEqual(by_key, {"__underflow__": 0, "bucket_0": 1, "bucket_1": 2, OVERFLOW_KEY: 3})
        self.assertEqual(snapshot.total_rows, 6)
        # 边界 20 的覆盖范围最小值就是 20，证明 20 没被 [10,20) 吞掉
        overflow = next(bucket for bucket in snapshot.buckets if bucket.key == OVERFLOW_KEY)
        self.assertEqual(overflow.value_min, 20.0)


class SnapshotTests(unittest.TestCase):
    def test_old_buckets_period_1_distribution(self):
        snapshot = make(PERIOD_1, OLD_EDGES, "S1")
        self.assertEqual(counts(snapshot), {"__underflow__": 0, "bucket_0": 1, "bucket_1": 1, OVERFLOW_KEY: 0})
        first = next(b for b in snapshot.buckets if b.key == "bucket_0")
        second = next(b for b in snapshot.buckets if b.key == "bucket_1")
        self.assertEqual((first.value_min, first.value_max), (5.0, 5.0))
        self.assertEqual((second.value_min, second.value_max), (19.0, 19.0))
        self.assertEqual(snapshot.total_rows, 2)
        self.assertEqual(snapshot.bucketed_rows, 2)
        self.assertEqual(snapshot.missing.count, 0)

    def test_old_buckets_period_2_overflow_distribution(self):
        snapshot = make(PERIOD_2, OLD_EDGES, "S2")
        self.assertEqual(counts(snapshot), {"__underflow__": 0, "bucket_0": 1, "bucket_1": 0, OVERFLOW_KEY: 2})
        overflow = next(b for b in snapshot.buckets if b.key == OVERFLOW_KEY)
        # 溢出区保留数值覆盖范围，能看清 20、35 的整体分布
        self.assertEqual((overflow.value_min, overflow.value_max), (20.0, 35.0))
        self.assertEqual(snapshot.total_rows, 3)

    def test_missing_values_counted_separately(self):
        records = [
            {"id": "a", "v": 5},
            {"id": "b", "v": None},
            {"id": "c", "v": ""},
            {"id": "d", "v": 25},
        ]
        snapshot = make(records, OLD_EDGES, "S3")
        self.assertEqual(snapshot.missing.count, 2)
        self.assertEqual(snapshot.missing.coordinates, ["b", "c"])
        self.assertEqual(snapshot.bucketed_rows, 2)
        # 总行数对得上：分桶行数 + 缺失行数 = 输入行数
        self.assertEqual(snapshot.bucketed_rows + snapshot.missing.count, len(records))
        self.assertEqual(snapshot.total_rows, len(records))

    def test_recompute_reproduces_original_snapshot(self):
        snapshot = make(PERIOD_2, OLD_EDGES, "S4")
        rebuilt = recompute_snapshot(snapshot, PERIOD_2)
        self.assertEqual(rebuilt.result_fingerprint, snapshot.result_fingerprint)
        self.assertEqual(rebuilt.to_dict()["buckets"], snapshot.to_dict()["buckets"])
        verification = verify_snapshot(snapshot, PERIOD_2)
        self.assertTrue(verification["ok"])

    def test_recompute_rejects_different_records(self):
        snapshot = make(PERIOD_1, OLD_EDGES, "S5")
        with self.assertRaises(ValueError):
            recompute_snapshot(snapshot, PERIOD_2)  # 输入指纹不同，拒绝复算
        verification = verify_snapshot(snapshot, PERIOD_2)
        self.assertFalse(verification["ok"])

    def test_fingerprint_stable_and_order_sensitive(self):
        fp1 = records_fingerprint(PERIOD_2, "id", "v")
        fp2 = records_fingerprint([dict(r) for r in PERIOD_2], "id", "v")
        fp3 = records_fingerprint(list(reversed(PERIOD_2)), "id", "v")
        self.assertEqual(fp1, fp2)  # 同内容同顺序，指纹稳定
        self.assertNotEqual(fp1, fp3)  # 重排后指纹不同

    def test_snapshot_roundtrip_serialization(self):
        snapshot = make(PERIOD_2, OLD_EDGES, "S6")
        restored = ChartSnapshot.from_dict(snapshot.to_dict())
        self.assertEqual(restored.result_fingerprint, snapshot.result_fingerprint)
        self.assertEqual(restored.spec.assign(35), OVERFLOW_KEY)

    def test_empty_buckets_kept_with_zero_rows(self):
        snapshot = make(PERIOD_1, OLD_EDGES, "S7")
        keys = [bucket.key for bucket in snapshot.buckets]
        overflow = next(b for b in snapshot.buckets if b.key == OVERFLOW_KEY)
        self.assertIn(OVERFLOW_KEY, keys)
        self.assertEqual(overflow.row_count, 0)
        self.assertIsNone(overflow.value_min)


class ComparisonTests(unittest.TestCase):
    def test_overflow_mode_old_buckets_reproducible_but_overflow_not_comparable(self):
        base = make(PERIOD_1, OLD_EDGES, "OLD-1")
        target = make(PERIOD_2, OLD_EDGES, "OLD-2")
        report = compare_snapshots(base, target, mode=MODE_OVERFLOW)
        rows = {row.key: row for row in report.rows}
        # 旧桶人数按原快照可复现，且两期边界一致，可直接比较
        self.assertTrue(rows["bucket_0"].directly_comparable)
        self.assertEqual((rows["bucket_0"].base_count, rows["bucket_0"].target_count), (1, 1))
        self.assertTrue(rows["bucket_1"].directly_comparable)
        self.assertEqual((rows["bucket_1"].base_count, rows["bucket_1"].target_count), (1, 0))
        # 溢出区是无界区间，不可直接比较
        self.assertFalse(rows[OVERFLOW_KEY].directly_comparable)
        self.assertEqual(rows[OVERFLOW_KEY].target_count, 2)
        self.assertEqual(rows[OVERFLOW_KEY].target_min, 20.0)
        self.assertEqual(rows[OVERFLOW_KEY].target_max, 35.0)
        non_comp = {item["key"] for item in report.non_comparable_intervals}
        self.assertEqual(non_comp, {OVERFLOW_KEY})
        # 两期总行数对不上：2 vs 3
        self.assertFalse(report.totals_match["equal"])
        self.assertEqual(report.totals_match["difference"], 1)
        for item in report.reconciliation:
            self.assertTrue(item["balanced"])

    def test_replay_mode_all_finite_buckets_comparable(self):
        base = make(PERIOD_1, NEW_EDGES, "NEW-1")
        target = make(PERIOD_2, NEW_EDGES, "NEW-2")
        report = compare_snapshots(base, target, mode=MODE_REPLAY)
        finite = [row for row in report.rows if row.kind == "finite"]
        self.assertTrue(all(row.directly_comparable for row in finite))
        rows = {row.key: row for row in report.rows}
        # 新桶 [20,30) 收 20，[30,40) 收 35，尾部有完整分辨率
        self.assertEqual(rows["bucket_2"].target_count, 1)
        self.assertEqual(rows["bucket_3"].target_count, 1)
        self.assertEqual(rows["bucket_2"].base_count, 0)
        self.assertEqual(rows["bucket_3"].base_count, 0)
        # 旧桶人数仍可在重放结果上还原：[0,10)=1、[10,20)=1（第一期）
        base_0_10 = rows["bucket_0"].base_count
        base_10_20 = rows["bucket_1"].base_count
        self.assertEqual((base_0_10, base_10_20), (1, 1))
        # 溢出桶两期都为空
        self.assertEqual(rows[OVERFLOW_KEY].base_count, 0)
        self.assertEqual(rows[OVERFLOW_KEY].target_count, 0)
        self.assertEqual(report.non_comparable_intervals, [])
        self.assertFalse(report.totals_match["equal"])

    def test_auto_mode_detection(self):
        base_old = make(PERIOD_1, OLD_EDGES, "A1")
        target_old = make(PERIOD_2, OLD_EDGES, "A2")
        self.assertEqual(compare_snapshots(base_old, target_old).mode, MODE_OVERFLOW)
        base_new = make(PERIOD_1, NEW_EDGES, "B1")
        target_new = make(PERIOD_2, NEW_EDGES, "B2")
        self.assertEqual(compare_snapshots(base_new, target_new).mode, MODE_REPLAY)

    def test_text_render_and_dict_roundtrip(self):
        base = make(PERIOD_1, NEW_EDGES, "T1")
        target = make(PERIOD_2, NEW_EDGES, "T2")
        report = compare_snapshots(base, target)
        text = render_report_text(report)
        self.assertIn("[0, 10)", text)
        self.assertIn("无法直接比较的区间", text)
        restored = report_from_dict(report.to_dict())
        self.assertEqual(render_report_text(restored), text)


if __name__ == "__main__":
    unittest.main()
