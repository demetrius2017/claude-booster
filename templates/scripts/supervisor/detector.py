#!/usr/bin/env python3
"""
Worker-state detector — adaptive silence + FSM + text accelerator.

Purpose:
  Consilium §5/Q3: decide WHEN a worker session is actually done vs.
  merely pausing. Fixed timeouts false-positive on slow models and
  false-negative on fast ones. Solution:
    1. Adaptive silence clamp = clamp(3 × median_event_gap, 20, 180)s.
    2. State machine over WorkerEvent stream
       (queued→running→{thinking, waiting_on_tool}→possibly_complete
        →{completed, failed, cancelled, blocked_by_quota}).
    3. Text heuristic ("Should I", "Can I", …) as accelerator ONLY —
       never authoritative, only lowers the silence threshold.

Contract:
  WorkerStateDetector()
    .on_event(ev: WorkerEvent) -> State     advance FSM
    .tick(now: float) -> State              re-check silence timeout
    .state -> State                         current
    .silence_budget(now) -> float           seconds until possibly_complete
    .force(state: State) -> None            external override (quota/cancel)

  Detector is transport-agnostic — consumes WorkerEvent only.

Limitations:
  - median seeded after ≥3 inter-event gaps; before that the 60s grace
    window after message_start prevents premature possibly_complete.
  - Text accelerator maps to a half-silence threshold (floor 20s),
    never to "completed" directly — supervisor still has to probe.
"""
from __future__ import annotations

import re
import statistics
import time
from dataclasses import dataclass, field
from enum import Enum

from .runtime import WorkerEvent

MIN_SILENCE = 20.0
MAX_SILENCE = 180.0
SILENCE_MULTIPLIER = 3.0
POST_START_GRACE = 60.0
MIN_GAPS_FOR_MEDIAN = 3

# Accelerator phrases — text-heuristic only, lowers threshold, never authoritative.
# Word-boundary anchored so "can ignore" does not match "can i".
ACCELERATOR_PATTERNS = [
    re.compile(r"\bshould i\b", re.IGNORECASE),
    re.compile(r"\bcan i\b", re.IGNORECASE),
    re.compile(r"\bwould you like\b", re.IGNORECASE),
    re.compile(r"\blet me know\b", re.IGNORECASE),
    re.compile(r"\bplease confirm\b", re.IGNORECASE),
    re.compile(r"\bwaiting for\b", re.IGNORECASE),
]


class State(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    THINKING = "thinking"
    WAITING_ON_TOOL = "waiting_on_tool"
    POSSIBLY_COMPLETE = "possibly_complete"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED_BY_QUOTA = "blocked_by_quota"


_TERMINAL: set[State] = {State.COMPLETED, State.FAILED, State.CANCELLED, State.BLOCKED_BY_QUOTA}


@dataclass
class WorkerStateDetector:
    """FSM + adaptive silence timeout driven by WorkerEvent stream."""

    state: State = State.QUEUED
    started_at: float | None = None
    last_event_at: float | None = None
    gaps: list[float] = field(default_factory=list)
    accelerator_active: bool = False
    forced_terminal: State | None = None

    def force(self, state: State) -> None:
        if state not in _TERMINAL:
            raise ValueError(f"force() only accepts terminal states, got {state}")
        self.state = state
        self.forced_terminal = state

    def on_event(self, ev: WorkerEvent) -> State:
        if self.forced_terminal is not None:
            return self.state
        now = ev.timestamp
        if self.last_event_at is not None:
            gap = now - self.last_event_at
            if gap > 0:
                self.gaps.append(gap)
        self.last_event_at = now

        kind = ev.kind
        if kind == "message_start":
            self.started_at = now
            self.state = State.RUNNING
        elif kind == "thinking_start":
            self.state = State.THINKING
        elif kind == "thinking_stop":
            if self.state is State.THINKING:
                self.state = State.RUNNING
        elif kind == "tool_use_start":
            self.state = State.WAITING_ON_TOOL
        elif kind == "tool_use_stop":
            if self.state is State.WAITING_ON_TOOL:
                self.state = State.RUNNING
        elif kind == "text_delta":
            text = (ev.payload or {}).get("text", "")
            if text and self._matches_accelerator(text):
                self.accelerator_active = True
            if self.state not in (State.THINKING, State.WAITING_ON_TOOL):
                self.state = State.RUNNING
        elif kind == "message_stop":
            subtype = (ev.payload or {}).get("subtype")
            self.state = State.COMPLETED if subtype == "success" else State.FAILED
        return self.state

    def tick(self, now: float) -> State:
        if self.state in _TERMINAL:
            return self.state
        if self.started_at is None or self.last_event_at is None:
            return self.state
        if (now - self.started_at) < POST_START_GRACE and len(self.gaps) < MIN_GAPS_FOR_MEDIAN:
            return self.state
        since_last = now - self.last_event_at
        if since_last >= self.silence_threshold():
            self.state = State.POSSIBLY_COMPLETE
        return self.state

    def silence_threshold(self) -> float:
        if len(self.gaps) >= MIN_GAPS_FOR_MEDIAN:
            base = SILENCE_MULTIPLIER * statistics.median(self.gaps)
            threshold = max(MIN_SILENCE, min(MAX_SILENCE, base))
        else:
            threshold = MAX_SILENCE
        if self.accelerator_active:
            threshold = max(MIN_SILENCE, threshold / 2.0)
        return threshold

    def silence_budget(self, now: float | None = None) -> float:
        if self.last_event_at is None:
            return self.silence_threshold()
        now = now if now is not None else time.monotonic()
        elapsed = now - self.last_event_at
        return max(0.0, self.silence_threshold() - elapsed)

    @staticmethod
    def _matches_accelerator(text: str) -> bool:
        return any(p.search(text) for p in ACCELERATOR_PATTERNS)
