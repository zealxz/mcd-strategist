"""营养配餐引擎：BMR/TDEE -> 每餐热量预算 -> 约束求解组合."""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field


@dataclass
class Profile:
    sex: str = "male"          # male / female
    age: int = 24
    height_cm: float = 175.0
    weight_kg: float = 70.0
    activity: float = 1.5      # 1.2 久坐 / 1.375 轻度 / 1.55 中度 / 1.725 高强度
    goal: str = "cut"          # cut 减脂 / bulk 增肌 / maintain 维持
    meals_per_day: int = 3

    @property
    def bmr(self) -> float:
        # Mifflin-St Jeor
        base = 10 * self.weight_kg + 6.25 * self.height_cm - 5 * self.age
        return base + (5 if self.sex == "male" else -161)

    @property
    def tdee(self) -> float:
        return self.bmr * self.activity

    @property
    def daily_kcal_budget(self) -> float:
        if self.goal == "cut":
            return self.tdee - 450
        if self.goal == "bulk":
            return self.tdee + 300
        return self.tdee

    @property
    def meal_kcal_budget(self) -> float:
        # 正餐占全天 35%，剩余分给加餐
        return self.daily_kcal_budget * 0.35

    @property
    def daily_protein_target(self) -> float:
        # 减脂 1.8g/kg，增肌 2.0g/kg，维持 1.4g/kg
        per_kg = {"cut": 1.8, "bulk": 2.0, "maintain": 1.4}[self.goal]
        return self.weight_kg * per_kg


# ----------------------------------------------------------------------
# 营养数据解析：MCP list-nutrition-foods 返回 markdown 表格式文本
# ----------------------------------------------------------------------

def parse_nutrition(mcp_text: str) -> list[dict]:
    """把 '品名,null,energyKj,kcal,protein,fat,carb,sodium,calcium' 行解析成 dict."""
    import json as _json
    import re as _re

    # MCP 返回 markdown 包裹的 JSON；先取 Original Response 里的 data
    body = mcp_text
    m = _re.search(r"## Original Response\s*(\{.*)", mcp_text, _re.S)
    if m:
        try:
            obj, _ = _json.JSONDecoder().raw_decode(m.group(1).strip())
            body = obj.get("data") or mcp_text
        except _json.JSONDecodeError:
            pass

    foods = []
    for line in body.splitlines():
        line = line.strip().lstrip("-").strip()
        if not line or "productName" in line or "[" in line[:2]:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 9:
            continue
        name = parts[0]
        if not name or name.lower() in ("null", "none"):
            continue
        try:
            foods.append(
                {
                    "name": name,
                    "energy_kcal": float(parts[3] or 0),
                    "protein": float(parts[4] or 0),
                    "fat": float(parts[5] or 0),
                    "carb": float(parts[6] or 0),
                    "sodium": float(parts[7] or 0),
                    "calcium": float(parts[8] or 0),
                }
            )
        except (ValueError, IndexError):
            continue
    return foods


@dataclass
class Combo:
    items: list[dict] = field(default_factory=list)

    @property
    def kcal(self) -> float:
        return sum(f["energy_kcal"] for f in self.items)

    @property
    def protein(self) -> float:
        return sum(f["protein"] for f in self.items)

    @property
    def names(self) -> list[str]:
        return [f["name"] for f in self.items]


_DRINK_KEYWORDS = ("咖啡", "美式", "拿铁", "可乐", "雪碧", "红茶", "柠檬", "奶茶", "冰茶", "鲜萃")
_MAIN_KEYWORDS = ("堡", "汉堡", "麦满分", "鸡", "卷", "排", "麦乐鸡", "翅")


def _is_drink(name: str) -> bool:
    return any(k in name for k in _DRINK_KEYWORDS)


def _is_main(name: str) -> bool:
    return any(k in name for k in _MAIN_KEYWORDS)


def recommend_combos(
    foods: list[dict],
    profile: Profile,
    top_k: int = 5,
    item_limit: int = 3,
) -> list[Combo]:
    """在热量预算内穷举 1..item_limit 件组合，按 目标函数 排序.

    规则：
    - 组合必须含至少一个主食（堡/鸡/卷类），饮料最多 1 件
    - 热量落在预算的 55%~100% 才算"一顿饭"
    - 减脂重蛋白供能比与饱腹感；增肌重热量与蛋白总量；维持看均衡
    - top_k 内惩罚高度重合的组合，保证多样性
    """
    budget = profile.meal_kcal_budget
    pool = [f for f in foods if 0 < f["energy_kcal"] <= budget]
    mains = [f for f in pool if _is_main(f["name"])]
    others = [f for f in pool if not _is_main(f["name"])]

    scored: list[tuple[float, Combo]] = []
    for size in range(1, item_limit + 1):
        import itertools as _it
        for combo_items in _it.combinations(mains, size):
            for extra in _it.chain([()], _it.combinations(others, 1)):
                items = list(combo_items) + list(extra)
                if len(items) > item_limit:
                    continue
                if sum(1 for f in items if _is_drink(f["name"])) > 1:
                    continue
                c = Combo(items)
                if not (budget * 0.55 <= c.kcal <= budget):
                    continue
                prot_ratio = c.protein * 4 / c.kcal
                fillness = c.kcal / budget
                sodium = sum(f["sodium"] for f in c.items)
                if profile.goal == "cut":
                    score = prot_ratio * 60 + fillness * 20 - max(0.0, sodium - 1500) / 80
                elif profile.goal == "bulk":
                    score = c.protein * 1.2 + fillness * 10 - max(0.0, sodium - 1800) / 80
                else:
                    score = prot_ratio * 40 + fillness * 15 - max(0.0, sodium - 1500) / 80
                scored.append((score, c))
    scored.sort(key=lambda x: -x[0])

    out: list[Combo] = []
    for s, c in scored:
        if any(len(set(c.names) & set(prev.names)) >= max(1, len(c.names) - 1) for prev in out):
            continue  # 与已选组合几乎相同，跳过
        out.append(c)
        if len(out) >= top_k:
            break
    return out
