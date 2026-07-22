from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import SecretStr

from regent.application.permit_service import ClaimedPermit
from regent.domain.errors import DomainError, ErrorCode


@dataclass(frozen=True, slots=True)
class BrokerRequest:
    action: str
    target: str
    payload: dict[str, Any]
    secret_ref: str


@dataclass(frozen=True, slots=True)
class BrokerReceipt:
    outcome: str
    external_request_id: str | None
    detail: dict[str, Any]


class PrivilegedExecutor(Protocol):
    async def execute(
        self, *, request: BrokerRequest, credential: SecretStr, idempotency_key: str
    ) -> BrokerReceipt: ...


class SecretBroker(Protocol):
    async def execute(self, permit: ClaimedPermit, request: BrokerRequest) -> BrokerReceipt: ...


class IsolatedSecretBroker:
    """Resolves credentials inside the broker and only returns a redacted receipt."""

    def __init__(
        self,
        secrets: dict[str, SecretStr],
        executor: PrivilegedExecutor,
    ) -> None:
        self._secrets = secrets
        self._executor = executor

    async def execute(self, permit: ClaimedPermit, request: BrokerRequest) -> BrokerReceipt:
        binding = permit.binding
        if request.action != binding.action or request.target != binding.target:
            raise DomainError(ErrorCode.PERMIT_INVALID, "broker request does not match permit")
        credential = self._secrets.get(request.secret_ref)
        if credential is None:
            raise DomainError(ErrorCode.PERMIT_INVALID, "secret reference is unavailable")
        receipt = await self._executor.execute(
            request=request,
            credential=credential,
            idempotency_key=binding.idempotency_key,
        )
        serialized = str(receipt.detail)
        if credential.get_secret_value() in serialized:
            raise RuntimeError("privileged executor leaked a credential into its receipt")
        return receipt
