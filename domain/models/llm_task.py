"""LLM 任务类型 + 请求/响应 DTO

单一职责：类型定义，无逻辑。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class LLMTaskType(str, Enum):
    """LLM 任务分类 — 每类对应一种成本档位 + 模型偏好"""

    # 轻量任务（快 + 便宜）
    INTENT_PARSE = "intent_parse"                # 意图解析
    CHAT = "chat"                                # 自由聊天

    # 中等任务
    TECHNICAL_ANALYST = "technical_analyst"      # 技术面分析
    FUNDAMENTAL_ANALYST = "fundamental_analyst"  # 基本面分析
    NEWS_ANALYST = "news_analyst"                # 新闻分析
    CAPITAL_ANALYST = "capital_analyst"          # 资金面分析
    RECOMMEND_JUDGE = "recommend_judge"          # 推荐裁决
    PICK_STOCKS = "pick_stocks"                  # 选股（Deprecated，被 recommend 流程替代）
    QUICK_ANALYSIS = "quick_analysis"            # 快速分析（单次综合）

    # 重量任务（大模型 + 长上下文）
    BULL_RESEARCHER = "bull_researcher"          # 多头研究员
    BEAR_RESEARCHER = "bear_researcher"          # 空头研究员
    RISK_CONSERVATIVE = "risk_conservative"      # 保守型风控
    RISK_AGGRESSIVE = "risk_aggressive"          # 激进型风控
    RISK_NEUTRAL = "risk_neutral"                # 中性风控
    RISK_MANAGER = "risk_manager"                # 风控主管
    JUDGE = "judge"                              # 投资组合经理


@dataclass
class LLMRequest:
    """LLM 请求 DTO"""

    task_type: LLMTaskType
    system_prompt: str
    messages: list[dict]                         # OpenAI 格式 [{role, content}, ...]
    max_tokens: int = 4096
    temperature: float = 0.7
    wechat_id: str | None = None                 # 归属用户（用于用量统计）
    retries: int = 1                             # 单 provider 内部重试次数


@dataclass
class LLMResponse:
    """LLM 响应 DTO"""

    content: str
    provider: str                                # deepseek / qwen / minimax / ...
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    cost_cny: float = 0.0
    fallback_count: int = 0                      # 经历的 provider 降级次数


@dataclass
class ProviderConfig:
    """单个 provider 的配置"""

    name: str                                    # deepseek / qwen / minimax / openai
    base_url: str
    api_key_env: str                             # 环境变量名（不硬编码 key）
    enabled: bool = True


@dataclass
class TaskRouting:
    """单个 task_type 的路由规则"""

    primary_provider: str
    primary_model: str
    max_tokens: int = 4096
    temperature: float = 0.7
    fallback_provider: str | None = None         # 主 provider 失败时的备胎
    fallback_model: str | None = None


@dataclass
class ModelPricing:
    """模型计价（元/百万 token）"""

    provider: str
    model: str
    input_price_per_1m: float                    # 输入价格
    output_price_per_1m: float                   # 输出价格
