# Ops Environment

Regent keeps the **execution surface allowlisted** (`environment-heal-v1`).

What can evolve:

- Which allowlisted action to prefer for a given host symptom
- LESSONS.md under `harness-lessons/ops-environment/` written when a heal improves the machine

What must never evolve via free-form agent output:

- Inventing shell commands
- Disabling host guard / soft-pause under critical pressure
- Auto-publishing Core source changes without the self-improvement sandbox + human gate

Baseline MUST:

1. Measure disk / mem / load / preview venv counts
2. Run only registered heal actions
3. Re-measure; soft-pause ACTIVE goals if still unhealthy
4. Record successful reason→action pairs into heal memory
