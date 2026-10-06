"""订单处理示例模块（用于演示 Code Explain Agent）。

这是一个刻意保留了若干"坏味道"的示例文件，方便 Agent 在解释时
同时指出潜在问题：函数偏长、参数过多、过宽异常捕获等。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DISCOUNT_TABLE = {"normal": 0.0, "silver": 0.05, "gold": 0.12, "diamond": 0.2}
MAX_QUANTITY = 99


@dataclass
class OrderItem:
    """订单中的单项商品。"""

    sku: str
    price: float
    quantity: int = 1

    def subtotal(self) -> float:
        """计算小计金额。"""
        return self.price * self.quantity


@dataclass
class Order:
    """一次下单的完整信息。"""

    order_id: str
    user_level: str = "normal"
    items: list[OrderItem] = field(default_factory=list)
    coupon: float = 0.0
    paid: bool = False

    def total_before_discount(self) -> float:
        """折扣前的总金额。"""
        return sum(item.subtotal() for item in self.items)


def calculate_discount(order: Order) -> float:
    """根据会员等级计算折扣比例。

    未知等级时返回 0，不做任何折扣。
    """
    level = (order.user_level or "").lower()
    if level not in DISCOUNT_TABLE:
        logger.warning("未知会员等级：%s", level)
        return 0.0
    return DISCOUNT_TABLE[level]


def apply_coupon(amount: float, coupon: float, threshold: float = 100.0) -> float:
    """在满足门槛时抵扣优惠券，返回抵扣后的金额。"""
    if amount <= 0:
        return 0.0
    if amount < threshold:
        return amount
    return max(0.0, amount - coupon)


def validate_order(order: Order) -> list[str]:
    """校验订单合法性，返回错误列表（为空表示通过）。"""
    errors: list[str] = []
    if not order.order_id:
        errors.append("订单号不能为空")
    if not order.items:
        errors.append("订单至少需要一个商品")
    for item in order.items:
        if item.price < 0:
            errors.append(f"商品 {item.sku} 价格非法")
        if item.quantity <= 0:
            errors.append(f"商品 {item.sku} 数量必须为正")
        if item.quantity > MAX_QUANTITY:
            errors.append(f"商品 {item.sku} 数量超过上限 {MAX_QUANTITY}")
    if order.coupon < 0:
        errors.append("优惠券金额不能为负")
    return errors


def process_order(
    order: Order,
    threshold: float = 100.0,
    *,
    enable_coupon: bool = True,
    strict: bool = True,
    notify_user: bool = False,
    channel: str = "web",
) -> dict:
    """处理订单：校验 → 计算折扣 → 抵扣优惠券 → 生成结果。

    这是一个刻意写长的函数，用来演示 Agent 的"函数过长"坏味道识别。
    """
    result: dict = {"order_id": order.order_id, "ok": False, "amount": 0.0, "errors": []}

    errors = validate_order(order)
    if errors:
        result["errors"] = errors
        if strict:
            logger.error("订单 %s 校验失败：%s", order.order_id, errors)
            return result
        logger.warning("订单 %s 存在 %d 项问题，非严格模式继续处理", order.order_id, len(errors))

    try:
        amount = order.total_before_discount()
        rate = calculate_discount(order)
        amount = amount * (1 - rate)

        if enable_coupon:
            amount = apply_coupon(amount, order.coupon, threshold)

        amount = round(amount, 2)
        result["amount"] = amount
        result["ok"] = amount >= 0

        if channel not in {"web", "app", "mini"}:
            logger.warning("未知下单渠道：%s", channel)
        if notify_user and result["ok"]:
            logger.info("订单 %s 处理完成，应付 %.2f（渠道 %s）", order.order_id, amount, channel)
        elif notify_user:
            logger.info("订单 %s 处理失败：%s", order.order_id, result["errors"])

    except Exception:  # 裸 except：会吞掉 KeyboardInterrupt / SystemExit
        logger.exception("订单处理异常")
        result["errors"].append("处理过程发生未知异常")

    return result


def summarize(orders: list[Order]) -> dict[str, float]:
    """汇总一批订单的成交金额。"""
    summary: dict[str, float] = {"gross": 0.0, "net": 0.0}
    for order in orders:
        gross = order.total_before_discount()
        net = process_order(order)["amount"]
        summary["gross"] += gross
        summary["net"] += net
    return summary


def main() -> None:
    """模块入口：演示一次完整下单流程。"""
    order = Order(
        order_id="ORD-20260001",
        user_level="gold",
        items=[OrderItem("SKU-1", 89.0, 2), OrderItem("SKU-2", 25.5, 1)],
        coupon=20.0,
    )
    print(process_order(order, notify_user=True))


if __name__ == "__main__":
    main()
