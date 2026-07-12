"""LLM 成本计算器

单一职责：根据 provider + model + tokens 计算成本。
数据来源：LLMRoutingPolicy 里的 pricing 表。
"""

from domain.llm_routing_policy import LLMRoutingPolicy


class CostCalculator:
    """按 pricing 表计算 LLM 调用成本"""

    def __init__(self, policy: LLMRoutingPolicy):
        self._policy = policy

    def calc(
        self, provider: str, model: str,
        prompt_tokens: int, completion_tokens: int,
    ) -> float:
        """返回本次调用的成本（人民币元），未配置价格则返回 0"""
        pricing = self._policy.get_pricing(provider, model)
        if pricing is None:
            return 0.0

        input_cost = (prompt_tokens / 1_000_000) * pricing.input_price_per_1m
        output_cost = (completion_tokens / 1_000_000) * pricing.output_price_per_1m
        return round(input_cost + output_cost, 6)
