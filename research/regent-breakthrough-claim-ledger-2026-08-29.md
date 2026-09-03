# Claim-to-source ledger

| Claim family | Primary evidence | Publisher/date | URL | Confidence / notes |
|---|---|---|---|---|
| Coding-agent repository work is becoming infrastructure | Remote Agent Server; Issue Resolver; SWE-ReX | OpenHands / SWE-agent, accessed 2026-08-29 | https://docs.openhands.dev/sdk/guides/agent-server/overview ; https://github.com/All-Hands-AI/OpenHands/blob/main/openhands/resolver/README.md ; https://github.com/SWE-agent/SWE-ReX | High; official docs/repos |
| GitHub supports issue/PR-launched coding agents | About third-party coding agents | GitHub, accessed 2026-08-29 | https://docs.github.com/en/copilot/concepts/agents/about-third-party-coding-agents | High; official docs |
| Dependency update discovery and low-risk automerge are mature | Renovate docs/automerge; Dependabot security updates | Renovate/GitHub, accessed 2026-08-29 | https://docs.renovatebot.com/ ; https://docs.renovatebot.com/key-concepts/automerge/ ; https://docs.github.com/en/code-security/concepts/supply-chain-security/dependabot-security-updates | High; official docs; both condition automation on checks/policy |
| Telemetry-to-root-cause/patch/PR exists | Seer docs/API | Sentry, accessed 2026-08-29 | https://docs.sentry.io/product/ai-in-sentry/seer ; https://docs.sentry.io/api/seer/start-seer-issue-fix/ | High; first-party product docs |
| Open-source SRE investigation/remediation is emerging | Robusta/HolmesGPT | Robusta, accessed 2026-08-29 | https://github.com/robusta-dev/robusta ; https://github.com/robusta-dev/holmesgpt/releases | Medium-high; repo describes rule-based remediation and AI investigation |
| Progressive delivery can be metric-gated with rollback | Analysis & Progressive Delivery | Argo Project, accessed 2026-08-29 | https://argoproj.github.io/argo-rollouts/features/analysis/ | High; official docs |
| Telemetry has cross-signal semantic standards | Semantic Conventions 1.44.0 | OpenTelemetry, accessed 2026-08-29 | https://opentelemetry.io/docs/specs/semconv/ | High; official standard docs |
| Agent task horizon is rising but reliability remains bounded | Task-Completion Time Horizons | METR, 2026 | https://evals.alignment.org/time-horizons/ | High; original methodology/data; >16h caveat retained |
| Real-world SWE tasks remain harder than curated bug-fix tasks | SWE-Lancer | OpenAI, 2025-02-18 / update 2025-07-28 | https://openai.com/index/swe-lancer/ | High; original benchmark; historical rather than current leaderboard claim |
| Passing tests can still yield incorrect patches | What’s in a Benchmark? | ICSE-SEIP, 2026 | https://doi.org/10.1145/3786583.3786904 | High; peer-reviewed; used only for oracle/overfitting limitation |
| Regent production agent path is not qualified | STATUS.md; internal audit | Regent workspace, 2026-08 | local workspace | High for repository state; production server not independently inspected |
| Regent monitor targets preview/content behavior rather than production SLOs | runtime_behavior_monitor.py; behavior_monitor_tick.py | Regent workspace, 2026-08 | local workspace | High from code inspection |
