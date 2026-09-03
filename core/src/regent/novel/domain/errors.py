"""Novel Engine 领域错误与统一 API 错误 envelope（Tech-Spec §8）。

统一错误 envelope 含 code / message / request_id / retryable / available_actions。
越权访问不得泄露资源是否存在（G-12）：统一走 NotFound 而非 Forbidden。
"""

from __future__ import annotations

from typing import Any


class NovelError(Exception):
    """Novel 领域错误基类。"""

    status_code: int = 400
    code: str = "novel_error"
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        retryable: bool | None = None,
        available_actions: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        if retryable is not None:
            self.retryable = retryable
        self.available_actions = available_actions or []
        self.details = details or {}

    def to_payload(self, request_id: str | None = None) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "request_id": request_id,
                "retryable": self.retryable,
                "available_actions": self.available_actions,
                "details": self.details,
            }
        }


class Unauthenticated(NovelError):
    status_code = 401
    code = "unauthenticated"

    def __init__(self, message: str = "authentication required") -> None:
        super().__init__(message, available_actions=["sign_in"])


class NotFound(NovelError):
    """资源不存在 **或** 越权——两者对外不可区分（G-12）。"""

    status_code = 404
    code = "not_found"

    def __init__(self, message: str = "resource not found") -> None:
        super().__init__(message, available_actions=["go_back"])


class PermissionDenied(NovelError):
    """仅在**可以安全区分**时使用（如作品存在且属于他人，但仍返回 404 语义）。

    默认映射为 404 以不泄露存在性；仅对公开资源（分享链接）返回 403。
    """

    status_code = 404
    code = "not_found"

    def __init__(self, message: str = "resource not found") -> None:
        super().__init__(message, available_actions=["go_back"])


class Conflict(NovelError):
    status_code = 409
    code = "conflict"

    def __init__(
        self,
        message: str,
        *,
        current_version: int | None = None,
        conflict_summary: dict[str, Any] | None = None,
    ) -> None:
        details: dict[str, Any] = {}
        if current_version is not None:
            details["current_version"] = current_version
        if conflict_summary:
            details["conflict_summary"] = conflict_summary
        super().__init__(
            message,
            available_actions=["reload", "retry_with_current_version"],
            details=details,
        )


class IdempotencyConflict(NovelError):
    """同幂等键异参数（Tech-Spec §5）。"""

    status_code = 409
    code = "idempotency_key_reused"

    def __init__(self, key: str) -> None:
        super().__init__(
            f"idempotency key reused with different parameters: {key}",
            available_actions=["use_new_idempotency_key"],
        )


class ValidationFailed(NovelError):
    status_code = 422
    code = "validation_failed"


class InvalidState(NovelError):
    status_code = 409
    code = "invalid_state"

    def __init__(self, message: str, *, current: str | None = None) -> None:
        details = {"current_state": current} if current else {}
        super().__init__(message, available_actions=["reload"], details=details)


class QuotaExceeded(NovelError):
    status_code = 429
    code = "quota_exceeded"
    retryable = True

    def __init__(self, message: str = "quota exceeded", *, retry_after: int = 60) -> None:
        super().__init__(message, available_actions=["wait", "top_up"], details={})
        self.retry_after = retry_after


class ExportNoticeRequired(NovelError):
    """G-22：导出前须知未满足或条款版本过期。"""

    status_code = 409
    code = "export_notice_required"

    def __init__(self, notice_version: str) -> None:
        super().__init__(
            "export destination notice must be acknowledged before export",
            available_actions=["acknowledge_export_notice"],
            details={"notice_version": notice_version},
        )


class GuardViolation(NovelError):
    """架构守卫被违反——属于服务端缺陷，不是用户输入问题。"""

    status_code = 500
    code = "guard_violation"


class InfrastructureUnavailable(NovelError):
    status_code = 503
    code = "infrastructure_unavailable"
    retryable = True
