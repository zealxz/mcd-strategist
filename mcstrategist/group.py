"""团餐模式：多人偏好聚合 + 满减门槛凑单."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field


@dataclass
class Person:
    name: str
    likes: list[str] = field(default_factory=list)   # 爱吃的品名关键词
    dislikes: list[str] = field(default_factory=list)
    budget: float | None = None                      # 人均预算上限


@dataclass
class GroupSolution:
    plan: list[dict] = field(default_factory=list)   # [{person, item, price}]
    total: float = 0.0
    discount_tier: float = 0.0
    after_discount: float = 0.0

    def summary(self) -> str:
        lines = [f"合计 ¥{self.total:.1f}，命中满减 ¥-{self.discount_tier:.0f}，实付 ¥{self.after_discount:.1f}"]
        for row in self.plan:
            lines.append(f"  {row['person']}: {row['item']} ¥{row['price']:.1f}")
        return "\n".join(lines)


def parse_promotions(text: str) -> list[dict]:
    """解析 query-promotions 返回的满减规则（尽力而为）."""
    import re

    rules = []
    for line in text.splitlines():
        m = re.search(r"(满|减)(\d+(?:\.\d+)?)[^\d]*(\d+(?:\.\d+)?)", line)
        if m:
            rules.append({"line": line.strip()})
    return rules


def plan_group_order(
    menu: list[dict],                 # [{name, price, ...}]
    people: list[Person],
    promo_rules: list[dict] | None = None,
    max_extra_items: int = 4,
) -> GroupSolution:
    """给每个人选亲和度最高的品，然后看是否差一点够满减，凑最小补单.

    promo_rules: [{'threshold': 100, 'discount': 20}, ...] 未解析时用估算。
    """
    rules = [
        {"threshold": float(r.get("threshold", 0)), "discount": float(r.get("discount", 0))}
        for r in (promo_rules or [])
        if r.get("threshold") and r.get("discount")
    ]
    rules.sort(key=lambda r: -r["discount"])

    plan: list[dict] = []
    for p in people:
        scored = []
        for item in menu:
            name = item.get("name", "")
            if any(d and d in name for d in p.dislikes):
                continue
            s = sum(2.0 for lk in p.likes if lk in name) - 0.0
            if p.budget and item.get("price", 0) > p.budget:
                s -= 10
            scored.append((s, item))
        scored.sort(key=lambda x: (-x[0], x[1].get("price", 0)))
        if scored:
            best = scored[0][1]
            plan.append({"person": p.name, "item": best["name"], "price": best.get("price", 0)})

    total = sum(r["price"] for r in plan)

    # 满足最高可享受满减
    applicable = [r for r in rules if total >= r["threshold"]]
    best_rule = max(applicable, key=lambda r: r["discount"]) if applicable else None

    # 若差一点达到更高档，尝试补单
    if rules and not best_rule or (best_rule and any(r["discount"] > best_rule["discount"] for r in rules)):
        higher = [r for r in rules if r["threshold"] > total]
        higher.sort(key=lambda r: r["threshold"])
        if higher:
            target = higher[0]
            gap = target["threshold"] - total
            if gap <= 30 and max_extra_items > 0:  # 补单阈值 30 元内才值得
                cheap = sorted(menu, key=lambda m: m.get("price", 999))
                add_total = 0.0
                for item in cheap:
                    if add_total >= gap:
                        break
                    plan.append({"person": "凑单", "item": item["name"], "price": item.get("price", 0)})
                    add_total += item.get("price", 0)
                total = sum(r["price"] for r in plan)
                applicable = [r for r in rules if total >= r["threshold"]]
                best_rule = max(applicable, key=lambda r: r["discount"]) if applicable else None

    disc = best_rule["discount"] if best_rule else 0.0
    return GroupSolution(plan=plan, total=total, discount_tier=disc, after_discount=total - disc)
