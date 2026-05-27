"""持仓语境提取 — 用 LLM 从自然语言中识别并提取持仓数据"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "extract_position.txt"


def build_extract_prompt(user_message: str) -> str:
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    return template.format(user_message=user_message)


def parse_extraction_result(llm_response: str) -> dict | None:
    """解析 LLM 返回的 JSON，容错处理"""
    text = llm_response.strip()

    # 去掉 markdown code fence
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 尝试从文本中找 JSON
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end])
            except json.JSONDecodeError:
                logger.warning("无法解析持仓提取结果: %s", text[:200])
                return None
        else:
            return None

    if not data.get("has_position"):
        return None

    positions = data.get("positions", [])
    if not positions:
        return None

    # 校验字段
    valid = []
    for p in positions:
        code = p.get("stock_code")
        name = p.get("stock_name", "")
        if not code:
            continue
        valid.append({
            "stock_code": str(code),
            "stock_name": name or "",
            "shares": p.get("shares"),
            "cost_price": p.get("cost_price"),
        })

    return {"positions": valid} if valid else None
