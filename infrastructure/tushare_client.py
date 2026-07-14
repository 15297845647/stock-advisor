"""Tushare 期货数据客户端

提供期货日线行情、主力合约映射等接口。
需要 tushare token 且积分 >= 2000。
"""

import asyncio
import logging
import re
from datetime import date, timedelta
from functools import partial

from agent.config import get_tushare_config

logger = logging.getLogger(__name__)

# 品种别名 → (品种代码, 交易所, 中文名)
_FUTURES_ALIAS: dict[str, tuple[str, str, str]] = {
    "螺纹": ("RB", "SHFE", "螺纹钢"),
    "螺纹钢": ("RB", "SHFE", "螺纹钢"),
    "铁矿": ("I", "DCE", "铁矿石"),
    "铁矿石": ("I", "DCE", "铁矿石"),
    "原油": ("SC", "INE", "原油"),
    "黄金": ("AU", "SHFE", "黄金"),
    "白银": ("AG", "SHFE", "白银"),
    "铜": ("CU", "SHFE", "沪铜"),
    "沪铜": ("CU", "SHFE", "沪铜"),
    "豆粕": ("M", "DCE", "豆粕"),
    "焦煤": ("JM", "DCE", "焦煤"),
    "焦炭": ("J", "DCE", "焦炭"),
    "纯碱": ("SA", "CZCE", "纯碱"),
    "玻璃": ("FG", "CZCE", "玻璃"),
    "欧线": ("EC", "INE", "欧线集运"),
    "集运": ("EC", "INE", "欧线集运"),
    "欧线集运": ("EC", "INE", "欧线集运"),
    "天然气": ("LU", "INE", "低硫燃油"),
    "棕榈": ("P", "DCE", "棕榈油"),
    "棕榈油": ("P", "DCE", "棕榈油"),
    "橡胶": ("RU", "SHFE", "橡胶"),
    "沥青": ("BU", "SHFE", "沥青"),
    "甲醇": ("MA", "CZCE", "甲醇"),
    "乙二醇": ("EG", "DCE", "乙二醇"),
    "PTA": ("TA", "CZCE", "PTA"),
    "pta": ("TA", "CZCE", "PTA"),
    "豆油": ("Y", "DCE", "豆油"),
    "菜油": ("OI", "CZCE", "菜籽油"),
    "苹果": ("AP", "CZCE", "苹果"),
    "生猪": ("LH", "DCE", "生猪"),
    "锌": ("ZN", "SHFE", "沪锌"),
    "镍": ("NI", "SHFE", "沪镍"),
    "锡": ("SN", "SHFE", "沪锡"),
    "铝": ("AL", "SHFE", "沪铝"),
    # 股指期货
    "沪深300": ("IF", "CFFEX", "沪深300股指"),
    "上证50": ("IH", "CFFEX", "上证50股指"),
    "中证500": ("IC", "CFFEX", "中证500股指"),
    "中证1000": ("IM", "CFFEX", "中证1000股指"),
    # 国债期货
    "国债": ("T", "CFFEX", "10年期国债"),
    "十年国债": ("T", "CFFEX", "10年期国债"),
    # 补充商品期货
    "燃油": ("FU", "SHFE", "燃油"),
    "低硫燃油": ("LU", "INE", "低硫燃油"),
    "不锈钢": ("SS", "SHFE", "不锈钢"),
    "花生": ("PK", "CZCE", "花生"),
    "尿素": ("UR", "CZCE", "尿素"),
    "棉花": ("CF", "CZCE", "棉花"),
    "白糖": ("SR", "CZCE", "白糖"),
    "菜粕": ("RM", "CZCE", "菜粕"),
}


class TushareClient:
    """Tushare Pro 期货数据客户端"""

    def __init__(self):
        self._pro = None

    def _get_pro(self):
        """延迟初始化 tushare pro_api（每次读最新 token + http_url）"""
        import tushare as ts
        cfg = get_tushare_config()
        token = cfg.get("token", "").strip()
        if not token:
            raise ValueError("未配置 TUSHARE_TOKEN，请在后台配置管理中设置")
        pro = ts.pro_api(token)
        http_url = cfg.get("http_url", "").strip()
        if http_url:
            pro._DataApi__http_url = http_url
        return pro

    def resolve_futures(self, text: str) -> tuple[str, str, str, str | None] | None:
        """从文本识别期货品种。

        Returns:
            (品种代码, 交易所, 中文名, 合约月份 or None)
            合约月份示例: "2408" / "2501" / None(走主力合约)
        """
        # 先尝试匹配具体合约代码（如 EC2408, RB2501, rb2410）
        # 不用 \b — 中文字符在 Python 3 中属于 \w，导致边界失效
        m = re.search(r"(?<![A-Za-z])([A-Za-z]{1,3})(\d{4})(?!\d)", text)
        if m:
            prefix = m.group(1).upper()
            month = m.group(2)
            for _, (code, exchange, name) in _FUTURES_ALIAS.items():
                if code == prefix:
                    return code, exchange, name, month

        # 中文别名匹配（无具体合约，走主力）
        for alias, info in _FUTURES_ALIAS.items():
            if alias in text:
                return (*info, None)

        # 品种代码无月份（如 EC, RB, EC0）→ 主力
        m = re.search(r"(?<![A-Za-z])([A-Za-z]{1,3})\d{0,1}(?!\d)", text)
        if m:
            prefix = m.group(1).upper()
            for _, (code, exchange, name) in _FUTURES_ALIAS.items():
                if code == prefix:
                    return code, exchange, name, None
        return None

    async def get_main_contract(self, symbol: str, exchange: str) -> str | None:
        """获取品种的主力合约代码"""
        try:
            pro = self._get_pro()
            df = await asyncio.get_event_loop().run_in_executor(
                None,
                partial(pro.fut_mapping, symbol=symbol, fields="ts_code,mapping_ts_code,trade_date"),
            )
            if df is None or df.empty:
                return None
            # 最新一条的 mapping_ts_code 即主力合约
            return df.iloc[0]["mapping_ts_code"]
        except Exception as e:
            logger.warning("获取主力合约失败 %s: %s", symbol, e)
            return None

    async def get_futures_daily(self, ts_code: str, days: int = 60) -> list[dict]:
        """获取期货日线行情，返回按日期升序的 K 线列表"""
        try:
            pro = self._get_pro()
            end = date.today()
            start = end - timedelta(days=days + 30)  # 多拉一些覆盖非交易日

            df = await asyncio.get_event_loop().run_in_executor(
                None,
                partial(
                    pro.fut_daily,
                    ts_code=ts_code,
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                ),
            )
            if df is None or df.empty:
                return []

            df = df.sort_values("trade_date").tail(days)
            bars = []
            for _, r in df.iterrows():
                pre_settle = r.get("pre_settle", 0) or r.get("pre_close", 0)
                close = r["close"]
                chg_pct = round((close - pre_settle) / pre_settle * 100, 2) if pre_settle else 0

                bars.append({
                    "date": r["trade_date"],
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(close),
                    "volume": float(r.get("vol", 0)),
                    "oi": float(r.get("oi", 0)),
                    "change_pct": chg_pct,
                })
            return bars
        except Exception as e:
            logger.error("获取期货日线失败 %s: %s", ts_code, e)
            return []

    async def get_futures_snapshot(self, ts_code: str, name: str) -> dict | None:
        """获取期货最新一日数据作为快照"""
        bars = await self.get_futures_daily(ts_code, days=1)
        if not bars:
            return None
        latest = bars[-1]
        latest["ts_code"] = ts_code
        latest["name"] = name
        return latest
