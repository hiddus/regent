# ADR-0002: PostgreSQL is the P0 runtime fact source

- Status: Accepted
- Date: 2026-07-16

Committed aggregate state and append-only audit records are authoritative.
Outbox events trigger work but are not event sourcing. Queue claiming, timers,
leases, inbox deduplication, and optimistic versions use PostgreSQL.
