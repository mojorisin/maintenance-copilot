"""The record of one agent run: shared by the agent, the UI and the evals.

Keeping this a plain dataclass (not a LangGraph type) means the evals and
the UI don't depend on the agent framework.
"""

import re
from dataclasses import asdict, dataclass, field

# Citations are written inline as [source_id], e.g. [WO-00257] or
# [manual:gas-turbine-ad30#high-vibration-alarm]. Plain words such as [1]
# or [note] are ignored: an ID always contains a digit, ':' or '-'.
CITATION_RE = re.compile(r"\[([A-Za-z][\w:#/.\-]*[\d:#/\-][\w:#/.\-]*)\]")


@dataclass
class ToolCall:
    name: str
    args: dict
    result: dict
    latency_s: float = 0.0


@dataclass
class AgentRun:
    question: str
    answer: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    latency_s: float = 0.0
    error: str | None = None

    def citations(self) -> list[str]:
        """Source IDs cited in the answer, in order of first appearance."""
        return list(dict.fromkeys(CITATION_RE.findall(self.answer)))

    def available_sources(self) -> set[str]:
        """Every source ID any tool returned during this run."""
        return {s for call in self.tool_calls for s in call.result.get("sources", [])}

    def to_dict(self) -> dict:
        return asdict(self)
