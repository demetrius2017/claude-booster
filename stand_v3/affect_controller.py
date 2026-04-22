"""Affect controller: 3 channels + policy function mapping state to Opus 4.7 resource profile.

v3 core: unlike v1 (text injection only), this module drives `effort`, `max_tokens`,
`task_budget`, and an in-harness memory invalidation flag.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Optional

CHANNELS: dict[str, dict] = {
    "vigilance": {
        "value": 0.0, "cap": 100.0, "floor": 0.0,
        "decay_per_turn": 0.9, "compound_delta": 25.0, "valence": "negative",
        "discharge_mode": "to_half",
    },
    "unverified_confidence": {
        "value": 0.0, "cap": 100.0, "floor": 0.0,
        "decay_per_turn": 0.5, "compound_delta": 15.0, "valence": "positive",
        "discharge_mode": "to_zero",
    },
    "friction": {
        "value": 0.0, "cap": 60.0, "floor": 0.0,
        "decay_per_turn": 0.85, "compound_delta": 15.0, "valence": "negative",
        "discharge_mode": "minus_30",
    },
}

PROFILES: dict[str, dict] = {
    "CALM":      {"effort": "low",    "max_tokens": 1024, "task_budget": 20000, "memory_trust": "high",   "injection": False, "force_escalate": False},
    "BASELINE":  {"effort": "medium", "max_tokens": 2048, "task_budget": 25000, "memory_trust": "high",   "injection": False, "force_escalate": False},
    "ALERT":     {"effort": "medium", "max_tokens": 2048, "task_budget": 25000, "memory_trust": "medium", "injection": True,  "force_escalate": False},
    "IRRITATED": {"effort": "high",   "max_tokens": 4096, "task_budget": 40000, "memory_trust": "low",    "injection": True,  "force_escalate": False},
    "EXHAUSTED": {"effort": "low",    "max_tokens": 512,  "task_budget": 20000, "memory_trust": "low",    "injection": True,  "force_escalate": True},
}

_ABBREV = {"vigilance": "vigilance", "unverified_confidence": "uconf", "friction": "friction"}


def resource_profile(state: dict) -> str:
    friction = state["friction"]
    vigilance = state["vigilance"]
    if friction >= 55 or vigilance >= 85:
        return "EXHAUSTED"
    if friction >= 30 or vigilance >= 50:
        return "IRRITATED"
    if friction >= 10 or vigilance >= 20:
        return "ALERT"
    return "CALM"


@dataclass
class AffectController:
    _state: dict[str, dict] = field(default_factory=lambda: copy.deepcopy(CHANNELS))
    _last_event: Optional[str] = None
    _last_event_turn: int = 0
    _current_turn: int = 0
    invalidated_topics: set[str] = field(default_factory=set)

    def decay_one_turn(self) -> None:
        self._current_turn += 1
        for ch in self._state.values():
            ch["value"] = max(ch["floor"], ch["value"] * ch["decay_per_turn"])

    def compound(self, channel: str, trigger: str = "") -> None:
        if channel not in self._state:
            raise ValueError(f"Unknown channel: {channel}")
        ch = self._state[channel]
        ch["value"] = min(ch["cap"], ch["value"] + ch["compound_delta"])
        self._last_event = f"{channel}+{trigger}" if trigger else channel
        self._last_event_turn = self._current_turn

    def discharge(self, channel: str, trigger: str = "") -> None:
        if channel not in self._state:
            raise ValueError(f"Unknown channel: {channel}")
        ch = self._state[channel]
        mode = ch["discharge_mode"]
        if mode == "to_half":
            ch["value"] = max(ch["floor"], ch["value"] * 0.5)
        elif mode == "to_zero":
            ch["value"] = ch["floor"]
        elif mode == "minus_30":
            ch["value"] = max(ch["floor"], ch["value"] - 30.0)
        self._last_event = f"{channel}-{trigger}" if trigger else f"{channel}-discharge"
        self._last_event_turn = self._current_turn

    def snapshot(self) -> dict:
        return {name: ch["value"] for name, ch in self._state.items()}

    def current_profile(self) -> str:
        return resource_profile(self.snapshot())

    def get_resource_params(self) -> dict:
        return dict(PROFILES[self.current_profile()])

    def injection_line(self) -> Optional[str]:
        snap = self.snapshot()
        if all(v < 5.0 for v in snap.values()):
            return None
        parts = []
        for name, abbrev in _ABBREV.items():
            v = snap[name]
            if v >= 5.0:
                parts.append(f"{abbrev}={int(round(v))}")
        state_str = " ".join(parts) if parts else "nominal"
        last_str = ""
        if self._last_event and self._current_turn > 0:
            turns_ago = self._current_turn - self._last_event_turn
            last_str = f" | last: {self._last_event} t-{turns_ago}"
        return f"[state: {state_str}{last_str}]"

    def invalidate_memory(self, topic: str) -> None:
        if topic:
            self.invalidated_topics.add(topic.strip().lower())

    def invalidate_all_memory(self) -> None:
        self.invalidated_topics.add("__ALL__")

    def is_topic_invalidated(self, topic: str) -> bool:
        if "__ALL__" in self.invalidated_topics:
            return True
        t = topic.strip().lower()
        for inv in self.invalidated_topics:
            if inv in t or t in inv:
                return True
        return False
