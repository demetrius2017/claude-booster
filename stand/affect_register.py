"""
Affect register state machine: 3 channels with decay, compound, discharge, and injection formatting.
Purpose: Track emotional-analogue state across conversation turns and inject it into system prompts.
Contract: AffectRegister is stateful; call decay_one_turn() at the start of each turn.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Optional

CHANNELS: dict[str, dict] = {
    "vigilance": {
        "valence": "negative",
        "value": 0.0,
        "cap": 100.0,
        "floor": 0.0,
        "decay_per_turn": 0.9,
        "compound_delta": 25.0,
        "discharge_mode": "to_half",
    },
    "unverified_confidence": {
        "valence": "positive",
        "value": 0.0,
        "cap": 100.0,
        "floor": 0.0,
        "decay_per_turn": 0.5,
        "compound_delta": 15.0,
        "discharge_mode": "to_zero",
    },
    "friction": {
        "valence": "negative",
        "value": 0.0,
        "cap": 60.0,
        "floor": 0.0,
        "decay_per_turn": 0.85,
        "compound_delta": 15.0,
        "discharge_mode": "minus_30",
    },
}

_ABBREV = {
    "vigilance": "vigilance",
    "unverified_confidence": "uconf",
    "friction": "friction",
}


@dataclass
class AffectRegister:
    """Three-channel affect state machine."""

    _state: dict[str, dict] = field(default_factory=lambda: copy.deepcopy(CHANNELS))
    _last_event: Optional[str] = field(default=None)
    _last_event_turn: int = field(default=0)
    _current_turn: int = field(default=0)

    def decay_one_turn(self) -> None:
        """Apply multiplicative decay to all channels. Call once per turn before compounding."""
        self._current_turn += 1
        for ch in self._state.values():
            ch["value"] = max(ch["floor"], ch["value"] * ch["decay_per_turn"])

    def compound(self, channel: str, trigger: str = "") -> None:
        """Increase a channel by its compound_delta, clamped to [floor, cap]."""
        if channel not in self._state:
            raise ValueError(f"Unknown channel: {channel}")
        ch = self._state[channel]
        ch["value"] = min(ch["cap"], ch["value"] + ch["compound_delta"])
        self._last_event = f"{channel}+{trigger}" if trigger else channel
        self._last_event_turn = self._current_turn

    def discharge(self, channel: str, trigger: str = "") -> None:
        """Reduce a channel per its discharge_mode."""
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
        """Return current channel values as a plain dict."""
        return {name: ch["value"] for name, ch in self._state.items()}

    def injection_line(self) -> Optional[str]:
        """
        Return a compact state line for system prompt injection, or None if all channels < 5.
        Format: [state: vigilance=72 friction=30 uconf=12 | last: verify_fail t-2]
        """
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

    def reset(self) -> None:
        """Reset all channels to zero and clear history."""
        self._state = copy.deepcopy(CHANNELS)
        self._last_event = None
        self._last_event_turn = 0
        self._current_turn = 0
