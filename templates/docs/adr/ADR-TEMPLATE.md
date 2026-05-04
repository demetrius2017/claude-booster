# ADR-NNNN: [Decision title — one line, verb-first]

**Date:** YYYY-MM-DD
**Status:** Proposed | Accepted | Superseded by ADR-XXXX | Deprecated

---

## Decision

> One paragraph. What was decided. Written as a fact, not a proposal.
> Example: "We aggregate partial fills by order_id using VWAP before applying to positions, rather than applying each fill immediately."

---

## Context (forces)

> What made this a non-trivial choice? List the actual constraints and pressures.

- **Force 1:** [constraint]
- **Force 2:** [constraint]

---

## Consequences

### Good
- [positive outcome]

### Bad
- [negative outcome / trade-off]

### Constraint (things this locks in)
- [downstream dependency this creates]

---

## What NOT to change

> Explicit list of things downstream code depends on. If a proposed change violates any item here, escalate before merging.

1. [protected invariant]
2. [protected interface]
3. [protected convention]
