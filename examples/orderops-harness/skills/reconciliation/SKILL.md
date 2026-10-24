---
name: reconciliation
description: Reconcile operational records using the case's policy and schema
---
Read the schema before joining records. Inventory every order and the available
refund/warehouse evidence. Separate events from underlying transactions; examine
identifiers and state changes before calculating amounts. Apply the supplied
policy's precedence and evidence requirements. Keep a per-order evidence table
linking the observed records, arithmetic, finding, and proposed action. State
missing evidence explicitly. Do not infer that an empty event table is complete
unless the supplied coverage field says so.
