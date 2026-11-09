# Persistence Skill

- Prefer SQLite or a local file store under the workspace; never claim cloud DB without credentials.
- Empty state is valid: list/detail endpoints may return `[]` / 404 before the first write.
- Expose at least one create + one list path so Journey can prove persistence across requests.
- Do not seed fake users/cards to make a screenshot look full.
- Keep schema migrations trivial (create-if-missing) unless the Goal asks otherwise.
