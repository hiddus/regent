# ADR-0001: Start as a modular monolith

- Status: Accepted
- Date: 2026-07-16

Regent Core starts as one Python package and deployable API/Worker pair backed by
PostgreSQL. Domain code cannot import FastAPI, SQLAlchemy, provider SDKs, or
`apps/`. Architecture tests enforce the dependency rule.
