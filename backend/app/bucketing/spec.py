"""分桶口径：连续半开区间与边界归属。

口径采用一组严格递增的边界 ``edges`` 划分连续数值区间，规则固定为
左闭右开 ``[edge_i, edge_{i+1})``：

- 区间连续：前一个桶的上界就是后一个桶的下界，不留空隙；
- 边界数据只能入一个桶：边界值等于某个 ``edge`` 时，归入以该边界为
  下界的桶（``bisect_right``），例如 10 归入 ``[10, 20)`` 而不是 ``[0, 10)``；
- 低于最小边界的进入可选的下溢桶，高于等于最大边界的进入可选的上溢（溢出）桶；
- 缺失值不参与数值分桶，在快照里单独统计。
"""
from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from typing import Any

UNDERFLOW_KEY = "__underflow__"
OVERFLOW_KEY = "__overflow__"
MISSING_KEY = "__missing__"

# 边界归属策略固定为左闭右开，保留字段以便快照里完整记录口径。
BOUNDARY_POLICY = "left_closed_right_open"


def parse_number(raw: Any) -> float:
    """把来源字段解析成可分桶的浮点数。

    - ``None``、空字符串、纯空白、``NaN`` 视为缺失，抛 :class:`ValueError`，
      由聚合层计入缺失值统计；
    - 布尔值不接受（``True`` 被当成 1 会造成静默口径错误）；
    - 其余无法解析的字符串同样抛 :class:`ValueError`。
    """
    if raw is None:
        raise ValueError("缺失值")
    if isinstance(raw, bool):
        raise ValueError("布尔值不参与数值分桶")
    if isinstance(raw, int):
        return float(raw)
    if isinstance(raw, float):
        if math.isnan(raw):
            raise ValueError("NaN 视为缺失值")
        return raw
    text = str(raw).strip()
    if not text:
        raise ValueError("空字符串视为缺失值")
    value = float(text)  # 无法解析时抛 ValueError
    if math.isnan(value):
        raise ValueError("NaN 视为缺失值")
    return value


@dataclass(frozen=True)
class BucketingSpec:
    """一次分桶的完整口径，可序列化、可指纹化。

    ``edges=(e0, e1, ..., en)`` 生成 ``n`` 个有限桶
    ``[e0,e1), [e1,e2), ..., [e_{n-1},en)``，加上可选的下溢桶 ``(-inf, e0)``
    与上溢桶 ``[en, +inf)``。
    """

    edges: tuple[float, ...]
    include_underflow: bool = True
    include_overflow: bool = True
    boundary_policy: str = BOUNDARY_POLICY

    def __post_init__(self) -> None:
        if len(self.edges) < 2:
            raise ValueError("至少需要两个边界才能形成一个区间桶")
        if any(not math.isfinite(float(edge)) for edge in self.edges):
            raise ValueError("分桶边界必须是有限数值")
        ordered = tuple(float(edge) for edge in self.edges)
        if any(ordered[i] >= ordered[i + 1] for i in range(len(ordered) - 1)):
            raise ValueError("分桶边界必须严格递增，保证区间连续且不重叠")
        if self.boundary_policy != BOUNDARY_POLICY:
            raise ValueError(f"暂不支持的边界归属策略：{self.boundary_policy}")
        object.__setattr__(self, "edges", ordered)

    @classmethod
    def from_edges(cls, edges: list[float] | tuple[float, ...], **kwargs: Any) -> "BucketingSpec":
        return cls(edges=tuple(float(edge) for edge in edges), **kwargs)

    def assign(self, value: float) -> str:
        """把一个数值唯一地分配到某个桶，返回桶键。"""
        index = bisect_right(self.edges, value) - 1
        if index < 0:
            if not self.include_underflow:
                raise ValueError(f"数值 {value} 低于最小边界 {self.edges[0]}，口径未启用下溢桶")
            return UNDERFLOW_KEY
        if index >= len(self.edges) - 1:
            if not self.include_overflow:
                raise ValueError(f"数值 {value} 达到或超过最大边界 {self.edges[-1]}，口径未启用溢出桶")
            return OVERFLOW_KEY
        return f"bucket_{index}"

    def bucket_defs(self) -> list[dict[str, Any]]:
        """口径下全部桶的定义（含上下溢桶），顺序即快照统计行的顺序。"""
        defs: list[dict[str, Any]] = []
        if self.include_underflow:
            defs.append({
                "key": UNDERFLOW_KEY,
                "label": f"(-∞, {self._fmt(self.edges[0])})",
                "lower_exclusive": None,
                "upper_exclusive": self.edges[0],
                "open_ended": True,
                "kind": "underflow",
            })
        for index in range(len(self.edges) - 1):
            lower, upper = self.edges[index], self.edges[index + 1]
            defs.append({
                "key": f"bucket_{index}",
                "label": f"[{self._fmt(lower)}, {self._fmt(upper)})",
                "lower_inclusive": lower,
                "upper_exclusive": upper,
                "open_ended": False,
                "kind": "finite",
            })
        if self.include_overflow:
            defs.append({
                "key": OVERFLOW_KEY,
                "label": f"[{self._fmt(self.edges[-1])}, +∞)",
                "lower_inclusive": self.edges[-1],
                "upper_exclusive": None,
                "open_ended": True,
                "kind": "overflow",
            })
        return defs

    def label_of(self, key: str) -> str:
        for definition in self.bucket_defs():
            if definition["key"] == key:
                return str(definition["label"])
        return key

    def to_dict(self) -> dict[str, Any]:
        return {
            "edges": list(self.edges),
            "include_underflow": self.include_underflow,
            "include_overflow": self.include_overflow,
            "boundary_policy": self.boundary_policy,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BucketingSpec":
        return cls(
            edges=tuple(float(edge) for edge in payload["edges"]),
            include_underflow=bool(payload.get("include_underflow", True)),
            include_overflow=bool(payload.get("include_overflow", True)),
            boundary_policy=str(payload.get("boundary_policy", BOUNDARY_POLICY)),
        )

    @staticmethod
    def _fmt(value: float) -> str:
        """整数浮点去掉无意义的 ``.0``，让区间标签更易读。"""
        if value == int(value):
            return str(int(value))
        return str(value)
