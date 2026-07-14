"""养家选股策略配置 — 默认值定义与序列化

配置以 JSON 存库，本模块负责默认值兜底与类型归一，保证缺字段/脏数据时仍可用。
"""

from dataclasses import asdict, dataclass, fields

# 操作时点提示文本默认值（养家原话，后台可改）
DEFAULT_ADVICE_RULE3 = (
    "开盘30分钟看方向：高开不追、低开不慌，"
    "最好等股价站稳分时均线再下手，免得一进场就挨套。"
)
DEFAULT_ADVICE_RULE4 = "5日线是生命线：股价跌破5日线赶紧跑，别犹豫。"
DEFAULT_ADVICE_RULE5 = (
    "尾盘买（下午2:50后找机会进场），次日冲高就溜，"
    "赚个快餐钱也比被深套强。"
)


@dataclass
class YangjiaConfig:
    """养家最笨选股法的可调参数"""
    lookback_days: int = 30        # 涨停回溯窗口（天）
    max_boards: int = 3            # 排除连板数下限（>= 此值剔除）
    volume_ratio_min: float = 1.5  # 量比阈值（活跃度）
    candidate_cap: int = 40        # 阶段B候选池上限
    output_count: int = 5          # 最终输出条数
    advice_rule3: str = DEFAULT_ADVICE_RULE3
    advice_rule4: str = DEFAULT_ADVICE_RULE4
    advice_rule5: str = DEFAULT_ADVICE_RULE5

    @classmethod
    def from_dict(cls, data: dict | None) -> "YangjiaConfig":
        """从（可能不完整的）字典构建配置，缺失字段用默认值，逐字段类型归一"""
        cfg = cls()
        if not data:
            return cfg

        for f in fields(cls):
            if f.name not in data or data[f.name] is None:
                continue
            raw = data[f.name]
            default = getattr(cfg, f.name)
            setattr(cfg, f.name, _coerce(raw, default))
        return cfg

    def to_dict(self) -> dict:
        return asdict(self)


# 中长线操作纪律默认文本
DEFAULT_ML_ADVICE_HOLD = "中长线持有：围绕20日线持股，不因短期波动频繁进出。"
DEFAULT_ML_ADVICE_STOP = "离场信号：有效跌破20日线，或基本面转差（营收/利润下滑）再考虑减仓。"
DEFAULT_ML_ADVICE_ADD = "加仓时机：回踩不破均线且趋势完好可分批加仓，忌追高。"


@dataclass
class MidLongConfig:
    """中长线选股参数（均线趋势 + 基本面）"""
    require_ma_bull: bool = True    # 均线多头排列
    require_uptrend: bool = True    # 趋势向上
    min_roe: float = 8.0           # ROE 下限(%)
    max_pe: float = 60.0           # PE 上限（>0 才校验）
    min_revenue_growth: float = 0.0  # 营收同比增长下限(%)
    candidate_cap: int = 40
    output_count: int = 5
    advice_hold: str = DEFAULT_ML_ADVICE_HOLD
    advice_stop: str = DEFAULT_ML_ADVICE_STOP
    advice_add: str = DEFAULT_ML_ADVICE_ADD

    @classmethod
    def from_dict(cls, data: dict | None) -> "MidLongConfig":
        cfg = cls()
        if not data:
            return cfg
        for f in fields(cls):
            if f.name not in data or data[f.name] is None:
                continue
            setattr(cfg, f.name, _coerce(data[f.name], getattr(cfg, f.name)))
        return cfg

    def to_dict(self) -> dict:
        return asdict(self)


def _coerce(raw: object, default: object) -> object:
    """按默认值类型转换原始值，失败则回退默认值"""
    try:
        if isinstance(default, bool):
            return raw if isinstance(raw, bool) else str(raw).lower() in ("1", "true", "yes", "on")
        if isinstance(default, int):
            return int(raw)
        if isinstance(default, float):
            return float(raw)
        return str(raw)
    except (ValueError, TypeError):
        return default
