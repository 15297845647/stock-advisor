"""Versioned research contracts. JSON payloads never carry credentials."""
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
import hashlib
import json


def encode(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def json_text(value):
    return json.dumps(value, default=encode, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(json_text(value).encode()).hexdigest()


@dataclass
class EvidenceItem:
    evidence_id: str
    kind: str
    source: str
    fetched_at: str
    payload: dict
    source_url: str | None = None
    observed_at: str | None = None
    published_at: str | None = None
    available_at: str | None = None
    quality_flags: list[str] = field(default_factory=list)


@dataclass
class AnalysisSnapshot:
    snapshot_id: str
    stock_code: str
    as_of: datetime
    market_session: str
    quote: dict
    bars: list[dict]
    fundamentals: dict = field(default_factory=dict)
    news: list[dict] = field(default_factory=list)
    fund_flows: list[dict] = field(default_factory=list)
    evidence: list[EvidenceItem] = field(default_factory=list)
    quality_status: str = 'unchecked'
    missing_fields: list[str] = field(default_factory=list)
    content_hash: str = ''
    calendar_dates: list[str] = field(default_factory=list)
    security_status: dict = field(default_factory=dict)

    def seal(self):
        data = asdict(self)
        for key in ('snapshot_id', 'content_hash', 'as_of'):
            data.pop(key)
        # Retrieval time is not data identity; observed/publication times are retained.
        for item in data['evidence']:
            item.pop('fetched_at', None)
        data['quote'].pop('fetched_at', None)
        self.content_hash = digest(data)
        self.snapshot_id = self.content_hash
        return self

    @classmethod
    def from_dict(cls, value):
        d = dict(value)
        d['as_of'] = datetime.fromisoformat(d['as_of'])
        d['evidence'] = [EvidenceItem(**e) for e in d.get('evidence', [])]
        return cls(**d)


@dataclass
class ResearchDecision:
    decision_id: str
    run_id: str
    stock_code: str
    status: str = 'observe'
    horizon_days: int = 10
    rule_score: float = 0
    trigger_conditions: list[str] = field(default_factory=list)
    invalidation_conditions: list[str] = field(default_factory=list)
    supporting_evidence_ids: list[str] = field(default_factory=list)
    opposing_evidence_ids: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class AnalysisRun:
    run_id: str
    snapshot_id: str
    created_at: datetime
    depth: str
    strategy_version: str
    prompt_version: str
    model_id: str
    status: str
    decision: ResearchDecision
    report: str = ''
    stages: list[str] = field(default_factory=list)
    model_calls: list[dict] = field(default_factory=list)
    annotations: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    group: str = 'rules_ai'

    @classmethod
    def from_dict(cls, value):
        d = dict(value)
        d['created_at'] = datetime.fromisoformat(d['created_at'])
        d['decision'] = ResearchDecision(**d['decision'])
        return cls(**d)


@dataclass
class EvaluationResult:
    run_id: str
    horizon_days: int
    status: str
    metrics: dict = field(default_factory=dict)
    assumptions: dict = field(default_factory=dict)
    exclusion_reason: str | None = None
