from dataclasses import dataclass, field


@dataclass
class UserProfile:
    """用户画像"""
    wechat_id: str
    nickname: str = ""
    risk_level: str = "moderate"      # conservative / moderate / aggressive
    trade_style: str = "swing"        # day / swing / position


@dataclass
class ChatMessage:
    """单条对话记录"""
    role: str      # user / assistant
    content: str


@dataclass
class UserContext:
    """每次对话时组装的完整用户上下文"""
    profile: UserProfile
    memories: list[str] = field(default_factory=list)
    watchlist: list[str] = field(default_factory=list)
    recent_chat: list[ChatMessage] = field(default_factory=list)
