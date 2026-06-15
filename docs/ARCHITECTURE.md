# Artemis Architecture

## Four Planes
PLANE A — INTELLIGENCE: reads market data, surfaces signals
PLANE B — EXECUTION: acts on signals via partner APIs
PLANE C — NETWORK: connects importers and manufacturers
PLANE D — REVENUE: logs every monetisation event

## The Central Entity
PROGRAM links IMPORTER + CMT_FACTORY + PRODUCT_SPECIFICATION.
Every feature is scoped to a program.

## Change Protocol
See CHANGE_PROTOCOL.md — follow it for every schema or logic change.

## Execution Trigger Chain
1. Intelligence detects signal → creates opportunity record
2. Operator reviews and confirms in UI
3. Partner API executes
4. Result feeds back to intelligence layer
5. Revenue transaction logged

## Data Confidence Levels
Mode 1 (priors only):     0.60–0.68
Mode 2 (partial real):    0.70–0.82
Mode 3 (company data):    0.85–0.95
