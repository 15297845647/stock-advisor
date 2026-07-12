"""关键词字典 — 用于把用户中文诉求映射为筛选条件

单一职责：静态词表，无逻辑。
"""

# 交易风格 → 条件覆盖
STYLE_KEYWORDS: dict[str, dict] = {
    "短线": {
        "style": "day", "horizon_days": 3,
        "min_turnover": 3.0, "min_volume_ratio": 1.5,
        "sector_weight": 0.5,
    },
    "打板": {
        "style": "day", "horizon_days": 2,
        "min_turnover": 5.0, "min_volume_ratio": 2.0,
        "sector_weight": 0.6,
    },
    "活跃": {
        "min_turnover": 3.0, "min_volume_ratio": 1.3,
    },
    "波段": {
        "style": "swing", "horizon_days": 10,
        "sector_weight": 0.3,
    },
    "中线": {
        "style": "swing", "horizon_days": 20,
        "sector_weight": 0.2,
    },
    "长线": {
        "style": "long", "horizon_days": 60,
        "min_market_cap": 200e8, "max_pe": 30,
        "sector_weight": 0.1,
    },
    "价值": {
        "max_pe": 20, "max_pb": 3,
        "min_market_cap": 100e8,
    },
    "低估": {
        "max_pe": 25, "max_pb": 4,
    },
    "白马": {
        "max_pe": 30, "min_market_cap": 300e8,
        "min_roe": 12,
    },
    "蓝筹": {
        "min_market_cap": 500e8, "max_pe": 25,
    },
    "成长": {
        "min_revenue_growth": 20, "sector_weight": 0.35,
    },
    "题材": {
        "require_hot_sector": True, "sector_weight": 0.5,
    },
    "热门": {
        "require_hot_sector": True, "sector_weight": 0.5,
    },
    "龙头": {
        "min_market_cap": 100e8, "min_volume_ratio": 1.2,
        "sector_weight": 0.4,
    },
    "小盘": {
        "max_market_cap": 200e8,
    },
    "大盘": {
        "min_market_cap": 500e8,
    },
}

# 板块名词 → 板块关键词（用于匹配 AKShare 板块名）
SECTOR_KEYWORDS: dict[str, list[str]] = {
    "半导体": ["半导体", "芯片", "集成电路"],
    "新能源": ["锂电", "光伏", "储能", "新能源"],
    "AI": ["人工智能", "AI", "算力", "大模型"],
    "算力": ["算力", "服务器", "CPO", "光模块"],
    "医药": ["医药", "医疗", "生物"],
    "创新药": ["创新药", "CXO"],
    "军工": ["国防", "军工", "航天", "航空"],
    "机器人": ["机器人", "工业自动化"],
    "白酒": ["白酒"],
    "银行": ["银行", "券商"],
    "地产": ["房地产", "建筑"],
    "汽车": ["汽车", "整车", "汽配"],
    "有色": ["有色", "黄金", "铜"],
    "煤炭": ["煤炭", "采掘"],
    "钢铁": ["钢铁"],
    "电力": ["电力", "公用事业"],
}

# 黑名单诉求关键词
BLACKLIST_PATTERNS: list[tuple[str, dict]] = [
    ("不要煤炭", {"blacklist_sectors": ["煤炭"]}),
    ("不要 ST", {"exclude_st": True}),
    ("排除银行", {"blacklist_sectors": ["银行"]}),
]

# 排除本次已推荐（"再推一批" / "换一批"）
REFRESH_KEYWORDS: set[str] = {
    "再推", "换一批", "再来", "还有吗", "重新推", "再推一批",
}


# 风险偏好 → 条件覆盖
RISK_KEYWORDS: dict[str, dict] = {
    "conservative": {
        "min_market_cap": 200e8, "max_pe": 30,
        "max_change_pct": 5, "min_volume_ratio": 1.0,
    },
    "moderate": {
        "min_market_cap": 50e8, "max_pe": 60,
    },
    "aggressive": {
        # 无上限，允许小盘 + 高波动
    },
}
