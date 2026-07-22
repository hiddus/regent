# State Transition Contract Template

Every transition must be registered before implementation.

```text
command
aggregate_type / aggregate_id / expected_version
allowed_from / target_state
preconditions
transaction_writes
audit_type
outbox_event
error_code
idempotency_key
recovery_behavior
```

Minimum stable errors: `INVALID_STATE`, `VERSION_CONFLICT`, `ACTIVE_RUN_EXISTS`,
`PERMIT_REQUIRED`, `PERMIT_INVALID`, `RECONCILIATION_REQUIRED`,
`GOAL_TERMINAL`, and `POLICY_DENIED`.
