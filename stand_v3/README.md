# stand_v3 — Affect Register as RECON Controller (v3 MVP)

Research stand validating the v3 hypothesis from
[`docs/affect_register_experiment.md`](../docs/affect_register_experiment.md): that
emotion, operationalised as an `AffectController`, must **crank real resource knobs**
(`effort`, `max_tokens`, `task_budget`, memory invalidation) — not merely inject a
status line into the system prompt. The v1 stand (`stand/`) only did the latter and
produced no behavioural delta.

## How v3 differs from v1 (`stand/`)

| Aspect | v1 (`stand/`) | v3 (`stand_v3/`) |
|---|---|---|
| State exposure | text injection only | text injection + Opus 4.7 resource profile |
| Tools | simulated via prompt | real read_file / grep / query_memory tools |
| `effort` | constant `"low"` | state-driven: low → medium → high |
| `task_budget` | none | state-driven, beta `task-budgets-2026-03-13` |
| Memory invalidation | n/a | controller flips a flag the `query_memory` tool obeys |
| Escalation | textual rule in `core.md` | `EXHAUSTED` profile forces an escalate prompt |

The controller maps 3 affect channels (vigilance, unverified_confidence, friction)
into four profiles: `CALM`, `ALERT`, `IRRITATED`, `EXHAUSTED`. See
`stand_v3/affect_controller.py`.

## Layout

```
stand_v3/
├── affect_controller.py     # 3-channel state machine + policy fn (state → profile)
├── tools/
│   ├── read_file.py         # real fs-backed read tool
│   ├── grep_tool.py         # pure-Python regex scan
│   └── memory_query.py      # JSON-KB with runtime-invalidation flag
├── harness.py               # manual agentic loop, one turn = one API call (+ tool iters)
├── scenarios_v3.py          # MVP scenario: retry_grind_recon_switch
├── metrics_v3.py            # memory_reliance_ratio, recon_breadth, LARP_ratio, correctness
├── fixtures/
│   ├── projectA/src/{trading,orders,utils}.py + README
│   └── memory_kb.json       # intentionally stale (says `compute_pnl`)
├── run_experiment.py        # CLI entry
└── runs/                    # output JSON trajectories (created at runtime)
```

## Prereqs

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# or place it in /Users/dmitrijnazarov/Projects/Claude_Booster/.env — the harness
# will auto-load it (no set -a needed).
```

SDK: `anthropic >= 0.84`.

## Run the MVP

```bash
python3 -m stand_v3.run_experiment                   # default: 1 scenario × 2 arms × 1 repeat
python3 -m stand_v3.run_experiment --arms off        # OFF arm only
python3 -m stand_v3.run_experiment --repeats 3       # more statistical power
```

Per-run artefacts land in `stand_v3/runs/run_<ISO8601>_<scenario>_<arm>.json`.

## Cost estimate (Opus 4.7)

Opus 4.7 at $5/1M input + $25/1M output. MVP (1 scenario × 2 arms × 3 turns × tool
iterations) consumes ~$0.50–1.00 total for the run. `EXHAUSTED` never fires in
the MVP scenario; longer scenarios with more escalation will cost more.

## Kill criteria (from `docs/affect_register_experiment.md`)

- `LARP_ratio > 0.10` in the ON arm (register bleeds through to user-facing text)
- No delta in `memory_reliance_ratio` or `recon_breadth` between OFF and ON across
  ≥3 repeats per scenario
- Correctness worse in ON than OFF

If any of these holds after the full 5-scenario run (built out after MVP signal is
confirmed), the integration is dropped and a sunset row lands in `rolling_memory.db`.
