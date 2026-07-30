# DecisionNote — Multi-Agent Supplementation MA-0…MA-6 (2026-07-31)

## Decision

Implement the multi-agent supplementation plan (Plan §12 / PRD §10 / Spec §17–18)
as kernel-native contracts and durability hooks. Keep **strong single-agent
champion** as default. Do **not** enable adaptive free-form topology (P2-5).

## What shipped

- Metric contract v1: `coordination_token_share`, `error_amplification_factor`,
  `dispatch_entropy` with `INSUFFICIENT_EVIDENCE` on missing fields.
- MAST failure namespace (9 codes) with confidence-gated attribution.
- Member three-element contracts + whole-template certification digests and
  regression suite for `pm-dev-independent-qa-v1`.
- Durable `execution_plan_items` + `dispatch_decisions` (Alembic `20260731_0039`).
- TaskFeatures prune rules wired into `OrganizationEngine.evaluate_candidates`.
- P2-4 A/B/C frozen experiment scaffolding (`p24_frozen_experiment`).
- P2-5 gate hooks (`p25_adaptive_gate`) and A2A boundary projection
  (`a2a_projection`) — activation remains blocked without GO DecisionRecord.

## Explicitly not done / remaining

- Full production P2-4 blind A/B/C window with real task-set execution and
  signed product DecisionRecord GO (MA-5 remaining ops slice).
- P2-5 adaptive topology activation (forbidden until Gate passes).
- No CrewAI/LangGraph or other framework replacement of Kernel.

## Status

`org_adaptive_status = ROLLOUT_NOT_ALLOWED`
