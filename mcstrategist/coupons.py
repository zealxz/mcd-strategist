"""领券与叠券策略."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class CouponInfo:
    title: str
    status: str = ""   # 已领取 / 可领取 / ...

    @property
    def claimable(self) -> bool:
        return "可领取" in self.status


def parse_coupons(text: str) -> list[CouponInfo]:
    out: list[CouponInfo] = []
    title = None
    for line in text.splitlines():
        m = re.search(r"优惠券标题[:：]\s*(.+?)\s*$", line)
        if m:
            title = m.group(1).strip()
        m = re.search(r"状态[:：]\s*(.+?)\s*$", line)
        if m and title:
            out.append(CouponInfo(title=title, status=m.group(1).strip()))
            title = None
    return out


def bind_all(client) -> list[str]:
    """真实领取所有可领的券（免费操作），返回领取结果摘要."""
    result = client.auto_bind_coupons()
    return [line for line in result.splitlines() if line.strip()][:20]


def coupon_advice(coupons: list[CouponInfo], combo_names: list[str]) -> list[str]:
    """把用户卡包里的券和推荐组合粗配对，给出叠券建议."""
    advice = []
    for c in coupons:
        if c.claimable:
            advice.append(f"还有券没领：{c.title}（先执行 auto-bind）")
    named = "|".join(combo_names)
    for c in coupons:
        # 简单词面匹配：券名与组合中的品名有交集
        for kw in re.split(r"[+\s/x]", c.title):
            kw = kw.strip()
            if len(kw) >= 2 and kw in named:
                advice.append(f"下单时可叠加：{c.title} 匹配到 {kw}")
                break
    return advice
