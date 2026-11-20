"""图表分桶聚合接口的 HTTP 集成测试。

环境里安装了 fastapi/httpx 才运行，否则跳过；纯标准库环境下 ``test_bucketing``
里的引擎测试仍然完整覆盖业务口径。
"""
from __future__ import annotations

import unittest

try:  # pragma: no cover - 取决于运行环境是否装了 Web 依赖
    from fastapi.testclient import TestClient

    from app.main import app
except Exception:  # pragma: no cover - ImportError（无 fastapi）或 RuntimeError（缺 httpx）都跳过
    TestClient = None
    app = None


@unittest.skipIf(TestClient is None, "未安装 fastapi/httpx，跳过 HTTP 集成测试")
class ChartBucketingApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_sources_and_seed_snapshots(self) -> None:
        response = self.client.get("/api/chart-bucketing/sources")
        self.assertEqual(response.status_code, 200)
        names = {item["name"] for item in response.json()["items"]}
        self.assertEqual(names, {"period_1", "period_2", "demo_missing"})

        response = self.client.get("/api/chart-bucketing/snapshots")
        self.assertEqual(response.status_code, 200)
        ids = {item["snapshot_id"] for item in response.json()["items"]}
        self.assertIn("SNAP-B0001", ids)
        self.assertIn("SNAP-B0002", ids)

    def test_compare_overflow_and_replay(self) -> None:
        response = self.client.post(
            "/api/chart-bucketing/compare",
            json={"base_snapshot_id": "SNAP-B0001", "target_snapshot_id": "SNAP-B0002", "mode": "overflow"},
        )
        self.assertEqual(response.status_code, 200)
        report = response.json()
        rows = {row["key"]: row for row in report["rows"]}
        self.assertEqual(rows["bucket_0"]["base_count"], 1)
        self.assertEqual(rows["__overflow__"]["target_count"], 2)
        self.assertFalse(rows["__overflow__"]["directly_comparable"])
        self.assertEqual(
            {item["key"] for item in report["non_comparable_intervals"]},
            {"__overflow__"},
        )

        response = self.client.post(
            "/api/chart-bucketing/compare",
            json={"base_snapshot_id": "SNAP-B0003", "target_snapshot_id": "SNAP-B0004", "mode": "replay"},
        )
        report = response.json()
        finite = [row for row in report["rows"] if row["kind"] == "finite"]
        self.assertTrue(all(row["directly_comparable"] for row in finite))
        self.assertEqual(report["non_comparable_intervals"], [])

    def test_recompute_and_invalid_edges(self) -> None:
        response = self.client.post("/api/chart-bucketing/snapshots/SNAP-B0001/recompute")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

        response = self.client.post(
            "/api/chart-bucketing/snapshots",
            json={"source_name": "period_1", "edges": [20, 10], "chart_name": "坏口径"},
        )
        self.assertEqual(response.status_code, 400)

    def test_text_render(self) -> None:
        response = self.client.post(
            "/api/chart-bucketing/compare",
            json={
                "base_snapshot_id": "SNAP-B0001",
                "target_snapshot_id": "SNAP-B0002",
                "mode": "overflow",
                "as_text": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        text = response.text
        self.assertIn("无法直接比较的区间", text)
        self.assertIn("[20, +∞)", text)


if __name__ == "__main__":
    unittest.main()
