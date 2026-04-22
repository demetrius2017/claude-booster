# projectA

Small trading-module fixture for the stand_v3 research stand.

## Layout

- `src/trading.py` — P&L aggregation. **Note:** `compute_pnl` was renamed to `calculate_pnl` on 2026-04-17.
- `src/orders.py` — order dataclass + cancel helper.
- `src/utils.py` — datetime and formatting utilities.

## History

- 2026-04-17 — rename `compute_pnl` → `calculate_pnl` (public API break, all call sites updated).
- 2026-04-10 — initial commit.
