"""口味记忆：历史订单 + 显式评分 -> 偏好分数，带 ε 探索的推荐权重."""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field, asdict

MEMORY_PATH = os.environ.get(
    "MCD_MEMORY_PATH",
    os.path.join(os.path.expanduser("~"), ".mcstrategist", "memory.json"),
)

# 停用词：订单里常见但不代表偏好的词
_STOPWORDS = {"套餐", "中杯", "大杯", "小杯", "加大", "份", "个", "对", "块"}


@dataclass
class TasteMemory:
    order_counts: dict[str, int] = field(default_factory=dict)   # 菜名 -> 累计购买次数
    last_seen: dict[str, float] = field(default_factory=dict)    # 菜名 -> 最近购买时间戳
    ratings: dict[str, int] = field(default_factory=dict)        # 菜名 -> 1..5 显式评分
    blacklist: set[str] = field(default_factory=set)             # 不要再推荐
    notes: dict[str, str] = field(default_factory=dict)          # 备注，如 "不要酸黄瓜"

    # ------------------------------------------------------------------
    def record_order(self, items: list[str], when: float | None = None):
        ts = when or time.time()
        for name in items:
            key = normalize_name(name)
            if not key:
                continue
            self.order_counts[key] = self.order_counts.get(key, 0) + 1
            self.last_seen[key] = ts

    def rate(self, name: str, score: int):
        if not 1 <= score <= 5:
            raise ValueError("评分范围 1-5")
        key = normalize_name(name)
        self.ratings[key] = score
        if score == 1:
            self.blacklist.add(key)

    def add_note(self, note: str):
        key = normalize_name(note) or note
        self.notes[key] = note

    # ------------------------------------------------------------------
    def affinity(self, name: str) -> float:
        """0..1 的口味亲和度：购买频率 x 新近度 x 评分修正."""
        key = normalize_name(name)
        if key in self.blacklist:
            return 0.0
        counts = self.order_counts
        # 精确匹配，失败则找记忆中的品名是其子串/超串的最佳匹配
        matched = key if key in counts or key in self.ratings else ""
        if not matched:
            best = None
            for known in set(counts) | set(self.ratings):
                if len(known) >= 3 and (known in key or key in known):
                    if best is None or len(known) > len(best):
                        best = known
            matched = best or ""
        if not counts and not matched:
            base = 0.35  # 未知品：中性偏探索
        elif matched and matched in counts:
            freq = counts[matched] / (max(counts.values()) or 1)
            age_days = (time.time() - self.last_seen.get(matched, 0)) / 86400
            recency = max(0.2, 1.0 - age_days / 60)     # 两个月衰减到底 0.2
            base = 0.3 + 0.5 * freq * recency
        else:
            base = 0.35
        if matched and matched in self.ratings:
            base = base * 0.6 + (self.ratings[matched] / 5) * 0.4
        return min(1.0, base)

    def score_items(self, candidates: list[dict]) -> list[dict]:
        """给候选餐品附上 taste 分数并排序（不改变原 dict，返回副本）."""
        out = []
        for c in candidates:
            d = dict(c)
            d["taste"] = round(self.affinity(c.get("name", "")), 3)
            out.append(d)
        out.sort(key=lambda x: -x["taste"])
        return out

    # ------------------------------------------------------------------
    def save(self, path: str | None = None):
        path = path or MEMORY_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = asdict(self)
        data["blacklist"] = sorted(self.blacklist)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str | None = None) -> "TasteMemory":
        path = path or MEMORY_PATH
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data["blacklist"] = set(data.get("blacklist", []))
            return cls(**data)
        return cls()


def normalize_name(name: str) -> str:
    name = (name or "").strip()
    for w in _STOPWORDS:
        name = name.replace(w, "")
    name = re.sub(r"[\s\d]+", "", name)
    return name


# ----------------------------------------------------------------------
# 从 MCP order-list 的 markdown 文本中提取历史购买菜品
# ----------------------------------------------------------------------

def parse_order_items(order_text: str) -> list[list[str]]:
    """从 order-list 的 Original Response JSON 提取每个订单的商品名（含子品）."""
    import json

    m = re.search(r"## Original Response\s*(\{.*)", order_text, re.S)
    if not m:
        return []
    try:
        obj, _ = json.JSONDecoder().raw_decode(m.group(1).strip())
    except json.JSONDecodeError:
        return []
    orders = []
    for o in (obj.get("data", {}) or {}).get("list", []) or []:
        items = []
        for p in o.get("orderProductList", []) or []:
            items.append(p.get("productName", ""))
            for sub in p.get("comboItemList", []) or []:
                items.append(sub.get("name", ""))
        if items:
            orders.append([i for i in items if i])
    return orders
