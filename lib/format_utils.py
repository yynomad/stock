"""
格式化工具函数
"""


def fmt_price(price: float, sign: str, decimals: int = 2) -> str:
    """格式化价格，根据货币符号决定小数位数（日元无小数）。"""
    if sign == "¥":
        return f"¥{price:,.0f}"
    return f"{sign}{price:,.{decimals}f}"


def pct_str(val: float) -> str:
    """格式化百分比，强制带正负号。"""
    sign = "+" if val >= 0 else ""
    return f"{sign}{val*100:.2f}%"
