"""麦当劳 MCP Server 客户端 — 纯标准库实现 Streamable HTTP MCP 协议."""
import json
import os
import urllib.request

MCP_URL = os.environ.get("MCD_MCP_URL", "https://mcp.mcd.cn")


class McpError(RuntimeError):
    pass


class McdClient:
    """Minimal MCP client over Streamable HTTP (JSON-RPC single requests)."""

    def __init__(self, token: str | None = None, url: str | None = None):
        self.token = token or os.environ.get("MCD_MCP_TOKEN", "")
        self.url = url or MCP_URL
        if not self.token:
            raise McpError(
                "缺少 MCP Token：设置环境变量 MCD_MCP_TOKEN"
                "（在 https://open.mcd.cn/mcp 控制台获取）"
            )
        self._id = 0
        self._initialize()

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
        # Server may answer JSON or SSE stream; handle both.
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if line.startswith("{"):
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("id") == payload.get("id"):
                    return obj
        # Fall back to last JSON object found.
        for line in reversed(raw.splitlines()):
            line = line.strip().lstrip("data:").strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        raise McpError(f"无法解析响应: {raw[:300]}")

    def _call(self, method: str, **params):
        self._id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": method,
        }
        if params:
            payload["params"] = params
        obj = self._post(payload)
        if "error" in obj:
            raise McpError(f"MCP error: {obj['error']}")
        return obj.get("result", {})

    def _initialize(self):
        self._call(
            "initialize",
            protocolVersion="2025-03-26",
            capabilities={},
            clientInfo={"name": "mcstrategist", "version": "0.1.0"},
        )

    # ------------------------------------------------------------------
    def tools(self) -> list[dict]:
        return self._call("tools/list").get("tools", [])

    def tool(self, name: str, **arguments) -> str:
        """调用工具，返回 text 内容（服务端把 API 结果包在 markdown 里）."""
        result = self._call("tools/call", name=name, arguments=arguments or {})
        texts = [
            c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"
        ]
        if result.get("isError"):
            raise McpError("\n".join(texts) or "tool error")
        return "\n".join(texts)

    # ---------------- 常用工具封装 ----------------
    def my_account(self) -> str:
        return self.tool("query-my-account")

    def available_coupons(self) -> str:
        return self.tool("available-coupons")

    def auto_bind_coupons(self) -> str:
        return self.tool("auto-bind-coupons")

    def my_coupons(self) -> str:
        return self.tool("query-my-coupons")

    def nutrition_foods(self) -> str:
        return self.tool("list-nutrition-foods")

    def order_list(self) -> str:
        return self.tool("order-list")

    def nearby_stores(self, latitude: float, longitude: float, be_type: int = 1) -> str:
        return self.tool("query-nearby-stores", latitude=latitude, longitude=longitude, beType=be_type)

    def meals(self, store_code: str, order_type: int = 1) -> str:
        return self.tool("query-meals", storeCode=store_code, orderType=order_type)

    def meal_detail(self, store_code: str, product_id: str, order_type: int = 1) -> str:
        return self.tool("query-meal-detail", storeCode=store_code, productId=product_id, orderType=order_type)

    def store_coupons(self, store_code: str, order_type: int = 1) -> str:
        return self.tool("query-store-coupons", storeCode=store_code, orderType=order_type)

    def calculate_price(self, store_code: str, order_type: int, products, **kw) -> str:
        return self.tool(
            "calculate-price",
            storeCode=store_code,
            orderType=order_type,
            products=products,
            **kw,
        )

    def campaign_calendar(self) -> str:
        return self.tool("campaign-calendar")

    def create_order(self, **kw) -> str:
        return self.tool("create-order", **kw)
