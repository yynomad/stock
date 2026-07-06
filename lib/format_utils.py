"""
格式化工具函数
"""


def fmt_price(price: float, sign: str, decimals: int = 2) -> str:
    """格式化价格，根据货币符号决定小数位数。"""
    if sign == "¥":
        # 日元 → 0 位小数（如 ¥142）
        return f"¥{price:,.0f}"
    if sign == "CN¥":
        # 人民币 → 2 位小数（如 CN¥1,262.98）
        return f"CN¥{price:,.{decimals}f}"
    return f"{sign}{price:,.{decimals}f}"


def pct_str(val: float) -> str:
    """格式化百分比，强制带正负号。"""
    sign = "+" if val >= 0 else ""
    return f"{sign}{val*100:.2f}%"
