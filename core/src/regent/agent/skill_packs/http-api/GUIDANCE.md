# HTTP API Skill

- Routes must be callable with GET/POST as declared; return coherent status codes.
- Prefer JSON bodies for machine Journey; HTML can wrap the same handlers.
- Align smoke/readiness routes with Runtime Profile — do not invent `/health` unless Profile lists it.
- Error responses should be structured (`{"error": "..."}`) rather than bare stack traces in production paths.
- Keep path names stable across REVISE so accepted Journeys keep passing.
