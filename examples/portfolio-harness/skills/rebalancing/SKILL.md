---
name: rebalancing
description: Compute drift and propose target weights within the guardrail limits
---
Run scripts/portfolio_math.py to compute current weights and drift against targets. Propose target weights respecting max 20% single-position target weight and max 5 non-hold calls/day.
