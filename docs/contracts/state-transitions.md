# Goal, Work, and Run State Transition Registry

> Status: Frozen for S1 implementation  
> Date: 2026-07-16

The executable source of truth is
`core/src/regent/domain/transitions.py`. Every accepted transition increments
the aggregate version exactly once. Version mismatch is checked before state
validation.

## Goal

| From | Command | To |
|---|---|---|
| DRAFT | qualify | READY |
| READY | activate | ACTIVE |
| ACTIVE | pause | PAUSED |
| PAUSED | resume | ACTIVE |
| ACTIVE | wait_for_human | WAITING_HUMAN |
| WAITING_HUMAN | human_resolved | ACTIVE |
| WAITING_HUMAN | human_blocked | BLOCKED |
| WAITING_HUMAN | human_timeout_exhausted | EXHAUSTED |
| BLOCKED | replan | ACTIVE |
| ACTIVE | achieve | ACHIEVED |
| ACTIVE/BLOCKED | exhaust | EXHAUSTED |
| Any nonterminal | fail | FAILED |
| Any nonterminal | cancel | CANCELLED |

Terminal Goal transitions return `GOAL_TERMINAL`.

## Work

| From | Command | To |
|---|---|---|
| PLANNED | make_ready | READY |
| READY | start | RUNNING |
| RUNNING | request_evaluation | EVALUATING |
| EVALUATING | accept | ACCEPTED |
| EVALUATING | reject | REJECTED |
| REJECTED | retry | READY |
| READY | wait_for_human | WAITING_HUMAN |
| WAITING_HUMAN | human_resolved | READY |
| READY | block | BLOCKED |
| BLOCKED | unblock | READY |
| RUNNING | mark_unknown | UNKNOWN |
| Any nonterminal | cancel | CANCELLED |

`ACCEPTED` and `CANCELLED` are terminal. `UNKNOWN` requires reconciliation
and can only be cancelled; successful reconciliation creates a new Run or
corrective Work.

## Run

| From | Command | To |
|---|---|---|
| CREATED | request_permit | PERMIT_PENDING |
| PERMIT_PENDING | queue | QUEUED |
| PERMIT_PENDING | deny | DENIED |
| PERMIT_PENDING | expire | EXPIRED |
| QUEUED | claim | RUNNING |
| RUNNING | mark_executed | EXECUTED |
| RUNNING | mark_failed | FAILED |
| RUNNING | mark_unknown | UNKNOWN |
| Any nonterminal | cancel | CANCELLED |

All Run terminal states are immutable.
