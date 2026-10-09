"""一键下单流水线：菜单 x 营养 x 口味 x 券 -> 组合 -> 算价 -> (确认后) 下单."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .client import McdClient
from .coupons import parse_coupons
from .memory import TasteMemory
from .nutrition import Profile, recommend_combos, _is_main, _is_drink


def _extract_json(text: str):
    """MCP 工具返回 markdown 包 JSON，提取最外层 JSON 对象."""
    m = re.search(r"(\{\"success\".*)", text, re.S)
    if m:
        try:
            return json.JSONDecoder().raw_decode(m.group(1).strip())[0]
        except json.JSONDecodeError:
            pass
    m = re.search(r"## Original Response\s*(\{.*)", text, re.S)
    if m:
        try:
            return json.JSONDecoder().raw_decode(m.group(1).strip())[0]
        except json.JSONDecodeError:
            pass
    return {}


def load_menu(client: McdClient, store_code: str, be_type: int = 1, order_type: int = 1) -> list[dict]:
    """拉门店菜单并拍平成 [{code,name,price,category}]，过滤周边/非食品."""
    text = client.tool("query-meals", storeCode=store_code, orderType=order_type, beType=be_type)
    obj = _extract_json(text)
    data = obj.get("data") or {}
    meals = data.get("meals", {})
    cat_of = {}
    for cat in data.get("categories", []):
        for m in cat.get("meals", []):
            cat_of[m.get("code")] = cat.get("name", "")
    out = []
    for code, d in meals.items():
        name = d.get("name", "")
        if any(k in name for k in ("周边", "玩具", "卡", "盒")) and "堡" not in name and "鸡" not in name:
            continue
        try:
            price = float(d.get("currentPrice") or 0)
        except (TypeError, ValueError):
            price = 0.0
        out.append({
            "code": code,
            "name": name,
            "price": price,
            "category": cat_of.get(code, ""),
        })
    return out


def parse_card_coupons(text: str) -> list[dict]:
    """解析 query-my-coupons 的 markdown 输出 -> [{title, price_hint, expire, tags}]"""
    coupons = []
    cur = {}
    for line in text.splitlines():
        m = re.match(r"^##\s*(.+)$", line.strip())
        if m:
            if cur:
                coupons.append(cur)
            cur = {"title": m.group(1).strip()}
            continue
        m = re.search(r"\*\*优惠\*\*[:：]\s*(.+)$", line)
        if m:
            cur["offer"] = m.group(1).strip()
        m = re.search(r"\*\*有效期\*\*[:：]\s*(.+)$", line)
        if m:
            cur["expire"] = m.group(1).strip()
        m = re.search(r"\*\*标签\*\*[:：]\s*(.+)$", line)
        if m:
            cur["tags"] = m.group(1).strip()
    if cur:
        coupons.append(cur)
    return coupons


def coupon_price_value(c: dict) -> float:
    """从券描述里提一个数值权重，用于粗略排序（买一送一/免费 > 定价券）."""
    text = c.get("title", "") + " " + c.get("offer", "")
    if "免费" in text or "买一送一" in text or "第二件" in text and "0" in text:
        return 9.0
    m = re.search(r"(\d+(?:\.\d+)?)\s*元", text)
    if m:
        v = float(m.group(1))
        return max(1.0, 10 - v / 2)   # 越便宜的定价券权重越高
    return 3.0


@dataclass
class DayPlan:
    store: str = ""
    combos: list[dict] = field(default_factory=list)   # 完整推荐：含价格/券建议
    raw_coupons: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"门店：{self.store}"]
        for i, cb in enumerate(self.combos, 1):
            lines.append(
                f"{i}. {' + '.join(cb['names'])} | ¥{cb['price']:.1f} | {cb['kcal']:.0f} kcal "
                f"蛋白 {cb['protein']:.0f}g | 口味 {cb['taste']:.2f}"
                + (f" | 券: {cb['coupon']}" if cb.get("coupon") else "")
            )
        return "\n".join(lines)


def plan_day(
    client: McdClient,
    profile: Profile,
    store_code: str,
    memory: TasteMemory | None = None,
    be_type: int = 1,
    top_k: int = 5,
) -> DayPlan:
    """完整"今日吃什么"：菜单 -> 营养过滤 -> 口味重排 -> 匹配卡包券."""
    memory = memory or TasteMemory.load()
    menu = load_menu(client, store_code, be_type)
    foods_text = client.nutrition_foods()
    from .nutrition import parse_nutrition
    foods = parse_nutrition(foods_text)

    # 名称粗匹配：把菜单项和营养库对上（菜单套餐名常含品名）
    nutri_by_name = {}
    for f in foods:
        nutri_by_name[f["name"]] = f

    def nutri_for(menu_name: str):
        # 精确 -> 包含
        if menu_name in nutri_by_name:
            return nutri_by_name[menu_name]
        for n, f in nutri_by_name.items():
            if len(n) >= 3 and n in menu_name:
                return f
        return None

    # 候选：菜单里的单品（非套餐优先做营养计算单元）
    candidates = []
    for item in menu:
        nf = nutri_for(item["name"])
        if nf:
            candidates.append({
                "code": item["code"], "name": item["name"], "price": item["price"],
                "energy_kcal": nf["energy_kcal"], "protein": nf["protein"],
                "fat": nf["fat"], "carb": nf["carb"], "sodium": nf["sodium"],
            })
    # 菜单里有价格、营养库里没有的，作为"价格补齐"字典
    price_by_code = {m["code"]: m["price"] for m in menu}

    combos = recommend_combos(candidates, profile, top_k=top_k * 3)

    # 卡包券
    card = parse_card_coupons(client.my_coupons())
    card_sorted = sorted(card, key=coupon_price_value, reverse=True)

    plan = DayPlan(store=store_code, raw_coupons=card)
    used_names = set()
    scored_entries = []
    for c in combos:
        names = c.names
        price = 0.0
        ok = True
        for f in c.items:
            if "code" not in f:
                ok = False
                break
            price += f.get("price", 0)
        if not ok:
            continue
        # 匹配券：券名与组合品名词面交集
        coupon = ""
        for cp in card_sorted:
            title = cp.get("title", "")
            for kw in re.split(r"[+\s/x（）()]", title):
                kw = kw.strip()
                if len(kw) >= 2 and any(kw in n for n in names):
                    coupon = title
                    break
            if coupon:
                break
        taste = round(sum(memory.affinity(n) for n in names) / len(names), 2)
        scored_entries.append({
            "names": names,
            "codes": [f["code"] for f in c.items],
            "price": price,
            "kcal": c.kcal,
            "protein": c.protein,
            "taste": taste,
            "coupon": coupon,
        })
    # 综合排序：口味 45% + 营养位次 35% + 价格便宜 20%，有可叠券额外 +0.08
    max_price = max((e["price"] for e in scored_entries), default=1) or 1
    for rank, e in enumerate(scored_entries):
        nutrition_rank = 1 - rank / max(1, len(scored_entries) - 1) if len(scored_entries) > 1 else 1
        price_score = 1 - e["price"] / max_price
        e["score"] = round(0.45 * e["taste"] + 0.35 * nutrition_rank + 0.20 * price_score + (0.08 if e["coupon"] else 0), 3)
    scored_entries.sort(key=lambda x: -x["score"])
    seen_fragments = set()
    for e in scored_entries:
        frag = tuple(sorted(e["names"]))
        if any(len(set(frag) & set(prev)) >= max(1, len(frag) - 1) for prev in seen_fragments):
            continue
        seen_fragments.add(frag)
        plan.combos.append(e)
        if len(plan.combos) >= top_k:
            break
    return plan


def price_combo(client: McdClient, store_code: str, codes: list[str], be_type: int = 1) -> dict:
    """调 calculate-price 试算一单（不下单）."""
    items = [{"productCode": code, "quantity": 1} for code in codes]
    text = client.tool(
        "calculate-price", storeCode=store_code, orderType=1, beType=be_type,
        items=items, needTableware=False,
    )
    obj = _extract_json(text)
    d = obj.get("data") or {}
    return {
        "original": (d.get("originalPrice") or 0) / 100,
        "payable": (d.get("price") or 0) / 100,
        "discount": (d.get("discount") or 0) / 100,
    }


def place_order(client: McdClient, store_code: str, codes: list[str], be_type: int = 1,
                take_way: str = "take-in-store") -> dict:
    """真实下单（谨慎！）。到店自取。"""
    items = [{"productCode": code, "quantity": 1} for code in codes]
    text = client.tool(
        "create-order", storeCode=store_code, orderType=1, beType=be_type,
        items=items, needTableware=False, takeWayCode=take_way,
    )
    return _extract_json(text)
