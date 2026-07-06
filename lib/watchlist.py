"""
持仓管理模块
─────────────────────────────────────────────────────────────
持久化存储用户的监控标的列表到 watchlist.json，
支持 AI 动态添加/删除。
"""

import json
import os

WATCHLIST_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "watchlist.json",
)


def _guess_symbol_info(symbol: str) -> dict:
    """根据代码后缀推测货币符号和显示名。"""
    sym = symbol.upper()
    if sym.endswith(".SS") or sym.endswith(".SZ"):
        return {"currency_sign": "CN¥", "display_name": sym}
    if sym.endswith(".T"):
        return {"currency_sign": "¥", "display_name": sym}
    if sym.endswith(".SW"):
        return {"currency_sign": "€", "display_name": sym}
    return {"currency_sign": "$", "display_name": sym}


class WatchlistManager:
    """监控标的持久化管理器，数据存储在 watchlist.json。"""

    def __init__(self, filepath: str = None):
        self._filepath = filepath or WATCHLIST_FILE
        self._data: dict[str, dict] = {}
        self.load()

    # ── 持久化 ──────────────────────────────────────────────────────

    def load(self) -> None:
        """从 JSON 文件加载监控列表。"""
        try:
            with open(self._filepath) as f:
                self._data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}

    def save(self) -> None:
        """保存监控列表到 JSON 文件。"""
        os.makedirs(os.path.dirname(self._filepath), exist_ok=True)
        with open(self._filepath, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    # ── 增删改查 ────────────────────────────────────────────────────

    def add(self, symbol: str, display_name: str = None,
            currency_sign: str = None) -> dict:
        """添加一个标的到监控列表。已存在则更新信息。"""
        symbol = symbol.upper()
        info = dict(self._data.get(symbol, _guess_symbol_info(symbol)))
        if display_name:
            info["display_name"] = display_name
        if currency_sign:
            info["currency_sign"] = currency_sign
        self._data[symbol] = info
        self.save()
        return info

    def remove(self, symbol: str) -> bool:
        """从监控列表移除一个标的。返回是否成功移除。"""
        symbol = symbol.upper()
        if symbol in self._data:
            del self._data[symbol]
            self.save()
            return True
        return False

    def get(self, symbol: str) -> dict | None:
        """获取单个标的信息。"""
        return self._data.get(symbol.upper())

    def get_all(self) -> dict[str, dict]:
        """返回完整监控列表 {symbol: info} 的副本。"""
        return dict(self._data)

    def all_symbols(self):
        """返回所有标的代码列表。"""
        return list(self._data.keys())

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, symbol: str) -> bool:
        return symbol.upper() in self._data
