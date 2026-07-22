"""Verify DoD evidence in database."""
import paramiko

SERVER = "118.31.171.159"
USER = "root"
PASSWORD = "080900.UI"
GOAL_ID = "ade9bdb7-41cf-4c57-8033-3d5327af7d2c"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(SERVER, username=USER, password=PASSWORD, timeout=15)
PSQL = "docker exec regent-postgres psql -U regent -d regent -t -A"

def query(sql: str, label: str) -> None:
    print(f"\n=== {label} ===")
    cmd = f"""{PSQL} -c "{sql}" """
    _, stdout, stderr = client.exec_command(cmd, timeout=30)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    print(out if out else "(empty)")
    if err and "NOTICE" not in err:
        print(f"STDERR: {err[:200]}")

# DoD 1: Goal exists and ACTIVE
query(f"SELECT id,status,correlation_id FROM goals WHERE id='{GOAL_ID}'", "DoD1: Goal")

# DoD 2: GoalSpec, DiscoveryRound, Hypotheses, Decision
query(f"SELECT id,status,version FROM goal_specs WHERE goal_id='{GOAL_ID}' ORDER BY version DESC LIMIT 1", "DoD2: GoalSpec")
query(f"SELECT id,status,round FROM discovery_rounds WHERE goal_id='{GOAL_ID}'", "DoD2: DiscoveryRound")
query(f"SELECT count(*) FROM product_hypotheses ph JOIN discovery_rounds dr ON ph.round_id=dr.id WHERE dr.goal_id='{GOAL_ID}'", "DoD2: Hypothesis count")
query(f"SELECT hd.decision,hd.selected_hypothesis_id IS NOT NULL as has_selection FROM hypothesis_decisions hd JOIN discovery_rounds dr ON hd.round_id=dr.id WHERE dr.goal_id='{GOAL_ID}'", "DoD2: HypothesisDecision")

# DoD 3: RequirementRevision + CapabilityResolutionPlan
query(f"SELECT id,status,requirement_key FROM requirement_revisions WHERE goal_id='{GOAL_ID}'", "DoD3: RequirementRevision")
query(f"SELECT crp.status FROM capability_resolution_plans crp JOIN requirement_revisions rr ON crp.requirement_revision_id=rr.id WHERE rr.goal_id='{GOAL_ID}'", "DoD3: CapabilityResolutionPlan")

# DoD 4: GenerationPlan, GenerationRun, WorkspaceSnapshot
query(f"SELECT gp.status FROM generation_plans gp JOIN requirement_revisions rr ON gp.requirement_revision_id=rr.id WHERE rr.goal_id='{GOAL_ID}'", "DoD4: GenerationPlan")
query(f"SELECT gr.status,gr.attempt FROM generation_runs gr JOIN generation_plans gp ON gr.plan_id=gp.id JOIN requirement_revisions rr ON gp.requirement_revision_id=rr.id WHERE rr.goal_id='{GOAL_ID}'", "DoD4: GenerationRun")
query(f"SELECT ws.status FROM workspace_snapshots ws JOIN generation_runs gr ON ws.generation_run_id=gr.id JOIN generation_plans gp ON gr.plan_id=gp.id JOIN requirement_revisions rr ON gp.requirement_revision_id=rr.id WHERE rr.goal_id='{GOAL_ID}'", "DoD4: WorkspaceSnapshot")

# DoD 5: DependencyResolution, AppBuild, VerificationReport
query(f"SELECT dr.status FROM dependency_resolutions dr JOIN workspace_snapshots ws ON dr.workspace_snapshot_id=ws.id JOIN generation_runs gr ON ws.generation_run_id=gr.id JOIN generation_plans gp ON gr.plan_id=gp.id JOIN requirement_revisions rr ON gp.requirement_revision_id=rr.id WHERE rr.goal_id='{GOAL_ID}'", "DoD5: DependencyResolution")
query(f"SELECT ab.status FROM app_builds ab JOIN dependency_resolutions dr ON ab.dependency_resolution_id=dr.id JOIN workspace_snapshots ws ON dr.workspace_snapshot_id=ws.id JOIN generation_runs gr ON ws.generation_run_id=gr.id JOIN generation_plans gp ON gr.plan_id=gp.id JOIN requirement_revisions rr ON gp.requirement_revision_id=rr.id WHERE rr.goal_id='{GOAL_ID}'", "DoD5: AppBuild")
query(f"SELECT vr.passed FROM verification_reports vr JOIN app_builds ab ON vr.app_build_id=ab.id JOIN dependency_resolutions dr ON ab.dependency_resolution_id=dr.id JOIN workspace_snapshots ws ON dr.workspace_snapshot_id=ws.id JOIN generation_runs gr ON ws.generation_run_id=gr.id JOIN generation_plans gp ON gr.plan_id=gp.id JOIN requirement_revisions rr ON gp.requirement_revision_id=rr.id WHERE rr.goal_id='{GOAL_ID}'", "DoD5: VerificationReport")

# DoD 6: ReleaseCandidate + Deployment
query(f"SELECT rc.status FROM release_candidates rc JOIN app_builds ab ON rc.app_build_id=ab.id JOIN dependency_resolutions dr ON ab.dependency_resolution_id=dr.id JOIN workspace_snapshots ws ON dr.workspace_snapshot_id=ws.id JOIN generation_runs gr ON ws.generation_run_id=gr.id JOIN generation_plans gp ON gr.plan_id=gp.id JOIN requirement_revisions rr ON gp.requirement_revision_id=rr.id WHERE rr.goal_id='{GOAL_ID}'", "DoD6: ReleaseCandidate")
query(f"SELECT d.status,d.environment FROM deployments d JOIN release_candidates rc ON d.release_candidate_id=rc.id JOIN app_builds ab ON rc.app_build_id=ab.id JOIN dependency_resolutions dr ON ab.dependency_resolution_id=dr.id JOIN workspace_snapshots ws ON dr.workspace_snapshot_id=ws.id JOIN generation_runs gr ON ws.generation_run_id=gr.id JOIN generation_plans gp ON gr.plan_id=gp.id JOIN requirement_revisions rr ON gp.requirement_revision_id=rr.id WHERE rr.goal_id='{GOAL_ID}'", "DoD6: Deployment")

# DoD 9: GateEvaluation + Decision
query(f"SELECT ge.status,ge.gate_result,ge.decision FROM gate_evaluations ge WHERE ge.goal_id='{GOAL_ID}'", "DoD9: GateEvaluation")

# DoD 10: Check outbox events for chain
query(f"SELECT event_type,status FROM outbox_events WHERE aggregate_id='{GOAL_ID}' ORDER BY occurred_at", "DoD10: Outbox Events")

client.close()
print("\n" + "=" * 60)
print("DoD Verification Complete")
