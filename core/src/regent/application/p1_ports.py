from typing import Any, Protocol

from pydantic import BaseModel, Field, model_validator

from regent.application.p1_contracts import FileChangeSet


class EvidenceSourceRequest(BaseModel):
    query: str = Field(min_length=1)
    source_types: list[str] = Field(default_factory=list)
    budget: dict[str, int | float] = Field(default_factory=dict)
    correlation_id: str = Field(min_length=1)


class EvidenceSourceSnapshot(BaseModel):
    source_uri: str = Field(min_length=1)
    captured_at: str = Field(min_length=1)
    content_artifact_uri: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    metadata: dict[str, Any] = Field(default_factory=dict)


class SandboxBuildRequest(BaseModel):
    workspace_snapshot_uri: str = Field(min_length=1)
    dependency_bundle_uri: str = Field(min_length=1)
    dependency_bundle_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_profile_ref: str = Field(min_length=1)
    runtime_profile_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)


class SandboxBuildResult(BaseModel):
    external_request_id: str = Field(min_length=1)
    status: str = Field(pattern=r"^(PASSED|FAILED|UNKNOWN)$")
    evidence_artifact_uri: str = Field(min_length=1)
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_artifact_uri: str | None = None
    build_artifact_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    checks: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_passed_artifact(self) -> "SandboxBuildResult":
        if self.status == "PASSED" and (
            self.build_artifact_uri is None or self.build_artifact_hash is None
        ):
            raise ValueError("PASSED build requires build artifact URI and hash")
        return self


class DeploymentRequest(BaseModel):
    build_artifact_uri: str = Field(min_length=1)
    environment: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)


class DeploymentResult(BaseModel):
    external_request_id: str = Field(min_length=1)
    status: str = Field(pattern=r"^(SUCCEEDED|FAILED|UNKNOWN)$")
    endpoint: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_succeeded_endpoint(self) -> "DeploymentResult":
        if self.status == "SUCCEEDED" and self.endpoint is None:
            raise ValueError("SUCCEEDED deployment requires endpoint")
        return self


class EvidenceSourceConnector(Protocol):
    async def fetch(self, request: EvidenceSourceRequest) -> list[EvidenceSourceSnapshot]: ...


class SandboxDriver(Protocol):
    async def build(self, request: SandboxBuildRequest) -> SandboxBuildResult: ...

    async def query(self, external_request_id: str) -> SandboxBuildResult: ...


class DeploymentProvider(Protocol):
    async def deploy(self, request: DeploymentRequest) -> DeploymentResult: ...

    async def query(self, external_request_id: str) -> DeploymentResult: ...

    async def rollback(self, external_request_id: str, correlation_id: str) -> DeploymentResult: ...


class GeneratedFileChangeSet(BaseModel):
    output: FileChangeSet
    model_ref: str = Field(min_length=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class FileChangeSetGenerator(Protocol):
    async def generate(self, plan: dict[str, Any]) -> GeneratedFileChangeSet: ...


class DependencyMaterializationRequest(BaseModel):
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    dependency_intents: list[dict[str, Any]] = Field(default_factory=list)
    runtime_profile_ref: str = Field(min_length=1)
    permit_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)


class DependencyMaterializationResult(BaseModel):
    status: str = Field(pattern=r"^(MATERIALIZED|REJECTED|FAILED|UNKNOWN)$")
    lockfile_uri: str | None = None
    bundle_uri: str | None = None
    bundle_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    sbom_uri: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    failure_code: str | None = None

    @model_validator(mode="after")
    def validate_materialized_bundle(self) -> "DependencyMaterializationResult":
        if self.status == "MATERIALIZED" and (
            self.lockfile_uri is None
            or self.bundle_uri is None
            or self.bundle_hash is None
            or self.sbom_uri is None
        ):
            raise ValueError("MATERIALIZED result requires lockfile, bundle and SBOM")
        return self


class DependencyMaterializer(Protocol):
    async def materialize(
        self, request: DependencyMaterializationRequest
    ) -> DependencyMaterializationResult: ...
