# Evidence Skill

- Provide one explicit ingest path (upload, URL fetch allowlisted, or paste) for the Goal's external evidence.
- Persist ingested evidence so a later Journey step can read it back.
- Validate content type / size lightly; fail closed with a clear error message.
- Do not invent private network hosts; stay within Profile `network_allowlist` when network is required.
- Record a short audit note (filename, bytes, timestamp) when evidence lands.
