import logging
from dataclasses import dataclass, field

logger = logging.getLogger("kizuna.tokens")


@dataclass
class TokenUsageEntry:
    node: str
    model: str
    prompt_tokens: int
    completion_tokens: int


@dataclass
class TokenTrace:
    """Per-turn record of every LLM call's token usage, tagged by the node
    (generation, safety_stage1, ...) that made it. PLAN's token-discipline
    rules require this trace to exist; surfacing it in a dashboard/metrics
    sink is deferred, so for now it's a single structured log line per turn.
    """

    turn_id: str
    entries: list[TokenUsageEntry] = field(default_factory=list)

    def record(self, node: str, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        self.entries.append(TokenUsageEntry(node, model, prompt_tokens, completion_tokens))

    @property
    def total_prompt_tokens(self) -> int:
        return sum(e.prompt_tokens for e in self.entries)

    @property
    def total_completion_tokens(self) -> int:
        return sum(e.completion_tokens for e in self.entries)

    def log(self) -> None:
        logger.info(
            "token_trace turn_id=%s total_prompt=%d total_completion=%d",
            self.turn_id,
            self.total_prompt_tokens,
            self.total_completion_tokens,
            extra={
                "turn_id": self.turn_id,
                "entries": [vars(e) for e in self.entries],
                "total_prompt_tokens": self.total_prompt_tokens,
                "total_completion_tokens": self.total_completion_tokens,
            },
        )
