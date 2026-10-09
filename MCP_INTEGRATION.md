# MCP Integration 文档

## 使用的 MCP Server

麦当劳中国官方 MCP Server（金拱门（中国）有限公司提供）

- 接入地址：`https://mcp.mcd.cn`
- 协议：Streamable HTTP（MCP `2025-03-26`）
- 鉴权：`Authorization: Bearer <MCP Token>`（open.mcd.cn/mcp 控制台申请）

## 实际调用的 Tools

| Tool | 用在哪 | 调用时机 |
|---|---|---|
| `auto-bind-coupons` | `coupons --bind` | 用户一键领券（真实写操作，免费） |
| `available-coupons` | `coupons` / `recommend` | 展示可领券状态 |
| `query-my-coupons` | `plan` | 读卡包券，与推荐组合匹配 |
| `list-nutrition-foods` | `recommend` / `plan` | 160 品营养素表 → 配餐约束求解 |
| `order-list` | `recommend --sync` | 历史订单 → 口味记忆初始化 |
| `query-nearby-stores` | `stores` | 附近门店与营业/预约信息 |
| `query-meals` | `plan` | 门店实时菜单（价格/分类/随单购） |
| `calculate-price` | `plan` | 推荐组合真实试算（原价/优惠/实付） |
| `create-order` | 显式要求时 | 真实下单（默认不调用） |

## 调用流程（以 `plan` 为例）

```
McdClient.initialize()
  └─ Streamable HTTP + Bearer Token，纯标准库实现

1. query-meals(storeCode, beType=1)      → 门店菜单拍平（过滤周边玩具）
2. list-nutrition-foods()                → 营养素表
3. 菜单品名 ↔ 营养库匹配（精确→子串）     → 带价格的候选集
4. 约束求解：每餐热量预算（BMR/TDEE 按目标折算）内穷举 1-3 件组合
5. query-my-coupons()                    → 卡包券
6. 综合排序 = 口味记忆45% + 营养位次35% + 价格20% + 可叠券加成
7. calculate-price(items=[推荐组合])      → 原价 → 优惠 → 实付
```

## 业务价值

- **对用户**：领券零遗漏、热量可控（减脂/增肌有数字依据）、不用每次纠结吃什么、真实价格试算防止到店翻车。
- **对麦当劳**：MCP 把「会员资产（券/积分）+ 菜单 + 营养数据」开放给 AI Agent，本项目展示了一个高频、可复购的消费场景闭环：**数据 → 个性化决策 → 交易**，全部经由官方 MCP 完成，为 MCP 生态提供了一个「工具组合大于单品」的范例。
