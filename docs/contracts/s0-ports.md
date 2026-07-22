# S0 Public Port Decisions

The first implementation will define provider-neutral ports for ModelProvider,
WorkspaceService, SandboxService, BuildService, TestService, ArtifactStore,
ObservationProvider, SecretBroker, and PolicyEngine.

S0 may create protocols and test doubles. Real external side effects are prohibited
until Permit and reconciliation contracts are implemented and tested.
