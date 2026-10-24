---
name: verify
description: Check output coverage, arithmetic and evidence before delivery
---
Parse both JSON outputs. Check every order appears exactly once in each array,
money values are nonnegative integers, and field names and enums match the
response contract. Reconcile each action with its finding and the current policy.
Verify every evidence reference names an existing data row. Review no-action and
escalation decisions as carefully as active proposals. Ensure report.md agrees
with the JSON and makes no claim of performing real actions.
