#!/usr/bin/env python3
"""
WorkerRuntime — transport-agnostic interface between supervisor and worker.

Purpose:
  Consilium §5/Q1 mandates a thin adapter boundary so switching from
  Path A (headless subprocess over stream-JSON) to an MCP-server
  topology is a transport swap, not a rewrite. This file owns the
  Protocol; implementations live in adapter modules (e.g.
  stream_json_adapter.py in the next session) and must not leak
  protocol specifics into policy.py / quota.py / detector.py.

Contract (Protocol):
  submit_task(prompt, system_prompt) -> task_id
  events(task_id) -> async-iterator of TerminalEvent | ToolEvent | TextEvent
  terminal_state(task_id) -> str | None       (None while running)
  tool_invocations(task_id) -> list[dict]     (observed tool calls)
  usage(task_id) -> dict | None               (input/output tokens)
  cancel(task_id) -> None
  shutdown() -> None

Migration triggers to MCP transport (consilium §5/Q1):
  1. SDK stream-JSON schema breaks in a minor bump.
  2. MCP-only tool appears that Booster needs.
  3. Anthropic documentation marks MCP as the stable headless path.
  4. Transport-specific bugs >20 %% of supervisor incidents at D60.

Limitations:
  Skeleton only — no implementations ship in this session. Concrete
  StreamJsonRuntime lands in Session 3 of the v1.2.0 roadmap.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Literal, Protocol, runtime_checkable


@dataclass(frozen=True)
class WorkerEvent:
    """Normalised worker event, transport-independent."""

    kind: Literal[
        "message_start",
        "text_delta",
        "tool_use_start",
        "tool_use_input",
        "tool_use_stop",
        "thinking_start",
        "thinking_stop",
        "message_stop",
        "usage",
    ]
    task_id: str
    timestamp: float  # monotonic seconds
    payload: dict  # transport-adapter-specific but documented per kind


@runtime_checkable
class WorkerRuntime(Protocol):
    """Transport-agnostic worker control surface.

    Implementations MUST be safe to call concurrently per-task_id but
    are NOT required to multiplex across tasks (supervisor MVP spawns
    one worker per session).
    """

    async def submit_task(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
    ) -> str:
        """Start a worker session. Returns task_id."""

    async def events(self, task_id: str) -> AsyncIterator[WorkerEvent]:
        """Async iterator of normalised events until terminal_state is set."""

    def terminal_state(self, task_id: str) -> str | None:
        """'completed' | 'failed' | 'cancelled' | 'blocked_by_quota' | None-while-running."""

    def tool_invocations(self, task_id: str) -> list[dict]:
        """Observed tool calls (from tool_use_start events)."""

    def usage(self, task_id: str) -> dict | None:
        """{input_tokens, output_tokens} or None if unavailable."""

    async def cancel(self, task_id: str) -> None:
        """Stop the worker (SIGINT → SIGTERM → SIGKILL ladder per Ops agent)."""

    async def shutdown(self) -> None:
        """Clean-exit all resources — must be idempotent."""
