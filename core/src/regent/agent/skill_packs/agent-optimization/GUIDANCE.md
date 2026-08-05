# Agent Optimization Skill

PenguinHarness-inspired self-evolution for Regent:

1. **Evaluate** — Live Preview QA + blocking delivery gaps (PM/Tech/UX).
2. **Diagnose** — cluster failures into skill-owned lessons (ui / product / http-api / runtime-contract).
3. **Edit** — write `harness-lessons/<skill>/LESSONS.md` (agent harness state), not model weights.
4. **Re-score** — keep only if score **strictly improves**; otherwise rollback to snapshot.

Never weaken gates, soft-pass without evidence, or skip product surface QA.
