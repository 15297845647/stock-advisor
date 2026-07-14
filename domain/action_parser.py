"""动作解析器 — 从 LLM 输出中提取结构化动作标记

支持的标记格式：
- [ACTION:DEEP_ANALYZE:代码]
"""

import re
from dataclasses import dataclass

_ACTION_RE = re.compile(r"\[ACTION:(\w+)(?::([^\]:]*))?(?::([^\]]*))?\]")


@dataclass
class ParsedAction:
    action: str       # DEEP_ANALYZE
    code: str | None
    name: str | None


def extract_actions(text: str) -> tuple[str, list[ParsedAction]]:
    """从 LLM 输出中提取动作标记，返回（清理后文本, 动作列表）"""
    actions = []
    for m in _ACTION_RE.finditer(text):
        actions.append(ParsedAction(
            action=m.group(1),
            code=m.group(2) or None,
            name=m.group(3) or None,
        ))

    clean_text = _ACTION_RE.sub("", text).rstrip()
    return clean_text, actions
