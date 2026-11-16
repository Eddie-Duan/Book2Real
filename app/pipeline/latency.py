"""回合时间线：所有延迟都从 speech_end 起算。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TurnTimeline:
    speech_end_ms: float = field(default_factory=lambda: time.time() * 1000)
    marks: dict[str, float] = field(default_factory=dict)
    data: dict = field(default_factory=dict)

    def mark(self, name: str, at_ms: float | None = None) -> float:
        value = at_ms if at_ms is not None else time.time() * 1000
        self.marks.setdefault(name, value)
        return value

    def since_speech_end(self, name: str) -> float:
        at = self.marks.get(name)
        if at is None:
            return -1.0
        return at - self.speech_end_ms

    def summary(self) -> dict:
        keys = [
            "asr_start",
            "asr_partial",
            "asr_final",
            "llm_start",
            "llm_first_token",
            "answer_first_clause",
            "tts_start",
            "first_audio",
            "filler_audio",
            "done",
        ]
        return {k: round(self.since_speech_end(k), 1) for k in keys if k in self.marks}
