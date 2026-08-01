# Test Harness Skill

- When Profile `require_tests` is true, add `tests/` with at least one route or persistence assertion.
- Prefer `pytest -q --tb=line` unless Profile overrides `test_command`.
- Failures should name the route and expected status/body slice.
- Do not mark skipped tests as success.
