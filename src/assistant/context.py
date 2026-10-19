from collections import deque
from dataclasses import dataclass, field


@dataclass
class AssistantContext:
    history: deque[dict[str, str]] = field(default_factory=deque)
    youtube_results: list[dict[str, str]] = field(default_factory=list)
    web_results: list[dict[str, str]] = field(default_factory=list)
    pending_web_open: bool = False
