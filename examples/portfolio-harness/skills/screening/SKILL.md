---
name: screening
description: Screen candidates from candidates.json against holdings
---
Read candidates.json, note universe_considered and universe_returned, and rank candidates by their screening metrics. Run scripts/portfolio_math.py for volatility when closes are available. Only advance candidates that beat their benchmark on snapshot evidence.
