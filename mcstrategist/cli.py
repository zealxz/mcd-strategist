#!/usr/bin/env python3
"""麦麦军师 CLI — 一条命令完成 领券 -> 配餐 -> 记忆 -> 建议."""
from __future__ import annotations

import argparse
import json
import os
import sys

from .client import McdClient
from .coupons import parse_coupons, coupon_advice
from .memory import TasteMemory, parse_order_items
from .nutrition import Profile, parse_nutrition, recommend_combos


def build_profile_from_config(path: str | None) -> Profile:
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        allowed = {k: v for k, v in data.items() if k in Profile.__dataclass_fields__}
        return Profile(**allowed)
    return Profile()


def cmd_coupons(client, args):
    if args.bind:
        print("== 一键领取所有可领券 ==")
        for line in client.auto_bind_coupons().splitlines():
            if line.strip():
                print(" ", line.strip()[:120])
    coupons = parse_coupons(client.available_coupons())
    total = len(coupons)
    claimable = [c for c in coupons if c.claimable]
    print(f"\n麦麦省共 {total} 张券，可领 {len(claimable)} 张：")
    for c in coupons:
        mark = "✅" if c.claimable else "·"
        print(f" {mark} {c.title} [{c.status}]")


def cmd_recommend(client, args):
    profile = build_profile_from_config(args.profile)
    if args.goal:
        profile.goal = args.goal

    print(f"== 身份档案 ==\n 目标: {profile.goal} | BMR {profile.bmr:.0f} | TDEE {profile.tdee:.0f} kcal/天"
          f" | 今日正餐预算 ≈{profile.meal_kcal_budget:.0f} kcal")

    mem = TasteMemory.load()
    if args.sync:
        print("\n== 同步历史订单到口味记忆 ==")
        orders = parse_order_items(client.order_list())
        n = 0
        for items in orders:
            mem.record_order(items)
            n += len(items)
        mem.save()
        print(f" 已同步 {len(orders)} 个订单 / {n} 件商品")

    foods = parse_nutrition(client.nutrition_foods())
    print(f"\n== 营养库 {len(foods)} 个品 ==")
    combos = recommend_combos(foods, profile, top_k=args.top)

    # 用口味记忆重排：综合分 = 营养分位(60%) + 口味(40%)
    ranked = []
    for i, c in enumerate(combos):
        taste = sum(mem.affinity(n) for n in c.names) / max(1, len(c.names))
        ranked.append((0.6 * (1 - i / max(1, len(combos))) + 0.4 * taste, c, taste))
    ranked.sort(key=lambda x: -x[0])

    print(f"\n== 今日推荐（目标 {profile.goal}，预算 ≈{profile.meal_kcal_budget:.0f} kcal）==")
    shown = []
    for score, c, taste in ranked[: args.top]:
        print(f" ★ {c.kcal:>4.0f} kcal | 蛋白 {c.protein:>4.1f}g | 口味 {taste:.2f} | {' + '.join(c.names)}")
        shown.append(c)

    # 叠券建议
    coupons = parse_coupons(client.available_coupons())
    if coupons:
        print("\n== 叠券建议 ==")
        for line in coupon_advice(coupons, shown[0].names if shown else []):
            print(" ", line)


def cmd_memory(client, args):
    mem = TasteMemory.load()
    if args.rate:
        name, _, score = args.rate.partition("=")
        mem.rate(name, int(score))
        mem.save()
        print(f"已记录评分：{name} = {score}")
        return
    if args.order:
        items = [x.strip() for x in args.order.split("+") if x.strip()]
        mem.record_order(items)
        mem.save()
        print(f"已记录用餐：{' + '.join(items)}")
        return
    print("== 口味记忆 ==")
    if not mem.order_counts and not mem.ratings:
        print(" （空）先执行 recommend --sync 或 memory --order '板烧鸡腿堡+中薯'")
        return
    for name, cnt in sorted(mem.order_counts.items(), key=lambda x: -x[1]):
        r = mem.ratings.get(name, "-")
        print(f"  {name}: 买过 {cnt} 次，评分 {r}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mcstrategist", description="麦麦军师")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("coupons", help="券管理：列表/一键领取")
    p.add_argument("--bind", action="store_true", help="真实领取所有可领的券")
    p.set_defaults(fn=cmd_coupons)

    p = sub.add_parser("recommend", help="今日吃什么（配餐+口味+叠券）")
    p.add_argument("--goal", choices=["cut", "bulk", "maintain"], help="覆盖目标")
    p.add_argument("--profile", help="profile.json 路径")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--sync", action="store_true", help="先同步历史订单")
    p.set_defaults(fn=cmd_recommend)

    p = sub.add_parser("memory", help="口味记忆管理")
    p.add_argument("--order", help="手动记录一餐: '板烧鸡腿堡+麦乐鸡'")
    p.add_argument("--rate", help="评分: '板烧鸡腿堡=5'")
    p.set_defaults(fn=cmd_memory)

    p = sub.add_parser("plan", help="完整今日方案：菜单x营养x口味x券 -> 算价")
    p.add_argument("--store", default=os.environ.get("MCD_STORE", "1420151"), help="门店编码")
    p.add_argument("--goal", choices=["cut", "bulk", "maintain"])
    p.add_argument("--profile", help="profile.json 路径")
    p.add_argument("--top", type=int, default=5)
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("stores", help="查附近门店")
    p.add_argument("--lat", type=float, default=22.5333)
    p.add_argument("--lng", type=float, default=113.95)
    p.set_defaults(fn=cmd_stores)

    p = sub.add_parser("points", help="积分管家：余额/到期预警/兑换性价比")
    p.set_defaults(fn=cmd_points)

    args = ap.parse_args(argv)
    client = McdClient()
    args.fn(client, args)


def cmd_stores(client, args):
    from .order import _extract_json
    text = client.nearby_stores(args.lat, args.lng, 1)
    obj = _extract_json(text)
    for s in obj.get("data", []):
        print(f"- {s.get('storeName')} | code {s.get('storeCode')} | {s.get('address','')} | 可预约:{s.get('reservation')}")


def cmd_points(client, args):
    from .points import PointsAccount, fetch_redeemables, best_deals, gap_report
    acct = PointsAccount.fetch(client)
    print("== 积分管家 ==")
    print(acct.summary())
    red = fetch_redeemables(client)
    print(f"\n== 积分商城（{len(red)} 个可兑，按性价比排序）==")
    deals = best_deals(acct.available, red)
    for r in deals:
        vps = f"{r['value_per_100']} 元/百分" if r.get("value_per_100") else "?"
        print(f" ✓ {r['name']} | {r['points']:g} 分 | {vps}")
    print("\n== 建议 ==")
    for line in gap_report(acct.available, red):
        print(" ", line)


def cmd_plan(client, args):
    from .order import plan_day, price_combo, parse_card_coupons
    from .memory import TasteMemory
    profile = build_profile_from_config(args.profile)
    if args.goal:
        profile.goal = args.goal
    # 到期预警
    expiring = [cp for cp in parse_card_coupons(client.my_coupons()) if cp.get("expires_today")]
    if expiring:
        print("⚠️ 今日到期券，别浪费：")
        for cp in expiring:
            print(f"   - {cp['title']}（{cp.get('offer','')}，{cp.get('expire','')[:40]}）")
        print()
    plan = plan_day(client, profile, args.store, TasteMemory.load(), top_k=args.top)
    print(f"== 门店 {plan.store} 今日方案（目标 {profile.goal}，预算 {profile.meal_kcal_budget:.0f} kcal）==")
    for i, cb in enumerate(plan.combos, 1):
        print(f" {i}. {' + '.join(cb['names'])}")
        print(f"    ¥{cb['price']:.1f} | {cb['kcal']:.0f} kcal | 蛋白 {cb['protein']:.0f}g | 口味 {cb['taste']:.2f}"
              + (f" | 可叠券: {cb['coupon']}" if cb['coupon'] else ""))
    if plan.combos:
        best = plan.combos[0]
        pr = price_combo(client, args.store, best["codes"])
        print(f"\n试算第 1 方案：原价 ¥{pr['original']:.1f} -> 实付 ¥{pr['payable']:.1f}（省 ¥{pr['discount']:.1f}）")
        print("确认下单请执行: python -m mcstrategist.cli order --store", args.store,
              "--codes", ",".join(best["codes"]))


if __name__ == "__main__":
    main()
