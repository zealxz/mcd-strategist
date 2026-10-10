"""积分管家：到期预警 + 兑换性价比排行 + 自动兑换."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .client import McdClient
from .order import _extract_json


@dataclass
class PointsAccount:
    available: float = 0.0
    expiring_this_month: float = 0.0
    expiring_next_month: float = 0.0
    expired_total: float = 0.0
    used: float = 0.0
    accumulative: float = 0.0

    @classmethod
    def fetch(cls, client: McdClient) -> "PointsAccount":
        text = client.my_account()
        obj = _extract_json(text)
        d = obj.get("data") or {}
        f = lambda k: float(d.get(k) or 0)
        return cls(
            available=f("availablePoint"),
            expiring_this_month=f("currentMouthExpirePoint"),
            expiring_next_month=f("nextMouthExpirePoint"),
            expired_total=f("expiredPoint"),
            used=f("usedPoint"),
            accumulative=f("accumulativePoint"),
        )

    def summary(self) -> str:
        lines = [
            f"可用积分：{self.available:g}（累计 {self.accumulative:g}，已用 {self.used:g}）",
        ]
        if self.expiring_this_month > 0:
            lines.append(f"⚠️ 本月将过期 {self.expiring_this_month:g} 分，尽快使用！")
        if self.expiring_next_month > 0:
            lines.append(f"下月将过期 {self.expiring_next_month:g} 分")
        if self.expired_total > 0 and self.expiring_this_month == 0 and self.expiring_next_month == 0:
            lines.append(f"（历史已过期 {self.expired_total:g} 分——积分不等人）")
        return "\n".join(lines)


def _price_of(name: str) -> float | None:
    """从商品名提取 'XX.X元' 的面值."""
    m = re.match(r"^(\d+(?:\.\d+)?)元", name)
    return float(m.group(1)) if m else None


def fetch_redeemables(client: McdClient, page_size: int = 50) -> list[dict]:
    """拉取积分商城可兑商品（餐品券类目），带上性价比."""
    text = client.tool("mall-points-products", categoryCode="1>4", pageSize=page_size)
    obj = _extract_json(text)
    items = obj.get("data") or []
    out = []
    for it in items:
        name = it.get("spuName") or ""
        pts = float(it.get("point") or 0)
        if pts <= 0:
            continue
        face = _price_of(name)
        out.append({
            "id": it.get("spuId"),
            "name": name,
            "points": pts,
            "face_value": face,
            # 每 100 积分换回多少元
            "value_per_100": round(face / pts * 100, 2) if face else None,
        })
    out.sort(key=lambda x: -(x["value_per_100"] or 0))
    return out


def best_deals(available: float, redeemables: list[dict], top_k: int = 8) -> list[dict]:
    return [r for r in redeemables if r["points"] <= available][:top_k]


def gap_report(available: float, redeemables: list[dict], top_k: int = 5) -> list[str]:
    """差多少分能兑到下一档，以及最值的兑换建议."""
    lines = []
    afford = [r for r in redeemables if r["points"] <= available]
    nxt = [r for r in redeemables if r["points"] > available]
    if afford:
        b = afford[0]
        if b.get("value_per_100"):
            lines.append(f"最优可兑：{b['name']}（{b['points']:g} 分，折合 {b['value_per_100']} 元/百分）")
        else:
            lines.append(f"最优可兑：{b['name']}（{b['points']:g} 分）")
    if nxt:
        n = nxt[0]
        gap = n["points"] - available
        if gap <= 300:
            lines.append(f"再攒 {gap:g} 分可兑：{n['name']}（{n['points']:g} 分）")
        else:
            lines.append(f"距离下一档 {n['name']} 还差 {gap:g} 分（{n['points']:g} 分档）")
    return lines


def redeem(client: McdClient, spu_id, sku_info: dict) -> dict:
    """真实兑换（写操作）。sku_info 需从 mall-product-detail 获取."""
    text = client.tool(
        "mall-create-order",
        shopId=2,
        spuId=spu_id,
        skuId=sku_info.get("skuId"),
        quantity=1,
        contactName=sku_info.get("contactName", ""),
        contactPhone=sku_info.get("contactPhone", ""),
    )
    return _extract_json(text)
