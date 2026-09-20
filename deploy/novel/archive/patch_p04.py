"""一次性补丁：P0-4 运行版本与租约隔离 + 调用配置指纹。"""
from __future__ import annotations

import pathlib

prod = pathlib.Path("core/src/regent/novel/application/production.py")
text = prod.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global text
    assert text.count(old) == 1, f"count={text.count(old)} for {old[:70]!r}"
    text = text.replace(old, new)


# --- 1) StaleLease 错误 ---
sub(
    '''class CallBudgetExceeded(CallError):''',
    '''class StaleLease(CallError):
    """租约已被别的 worker 接管（或已过期），本次结果不得写入。"""

    failure_code = "STALE_LEASE"
    retryable = True


class CallBudgetExceeded(CallError):''',
)

# --- 2) 配置指纹 ---
sub(
    """def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
""",
    '''def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def config_fingerprint(*, model: str, sampling: dict[str, Any] | None = None) -> str:
    """调用配置指纹：模型与采样参数参与同键判定（Tech-Spec §5）。

    只覆盖 prompt/context 会让「同键不同模型或不同 temperature」被当成同一次
    调用而复用或冲突判定失真，因此配置必须进指纹。
    """
    return _sha(_dump({"model": model, "sampling": dict(sampling or {})}))
''',
)

# --- 3) run() 传配置 ---
sub(
    """        logical_call_id = logical_call_key(run_id, command_id, candidate_id, purpose)
        prompt_hash = _sha(system_prompt)
        context_hash = _sha(user_prompt)
""",
    """        logical_call_id = logical_call_key(run_id, command_id, candidate_id, purpose)
        prompt_hash = _sha(system_prompt)
        context_hash = _sha(user_prompt)
        sampling = {"model": model_hint, "temperature": temperature}
        config_hash = config_fingerprint(model=model_hint, sampling=sampling)
""",
)

sub(
    """                prompt_hash=prompt_hash,
                context_hash=context_hash,
                model_hint=model_hint,
                prompt_chars=len(user_prompt),
                system_chars=len(system_prompt),
            )""",
    """                prompt_hash=prompt_hash,
                context_hash=context_hash,
                model_hint=model_hint,
                sampling=sampling,
                config_hash=config_hash,
                prompt_chars=len(user_prompt),
                system_chars=len(system_prompt),
            )""",
)

# --- 4) _prepare 签名与校验 ---
sub(
    """        model_hint: str,
        prompt_chars: int,
        system_chars: int,
    ) -> _Ticket | None:
        existing = await _latest_call(session, logical_call_id)
        if existing is not None:
            if existing.prompt_hash != prompt_hash or existing.context_hash != context_hash:
                raise CallConflict(
                    "same logical call id with different inputs; refusing to merge (§5)"
                )""",
    """        model_hint: str,
        prompt_chars: int,
        system_chars: int,
        sampling: dict[str, Any] | None = None,
        config_hash: str = "",
    ) -> _Ticket | None:
        existing = await _latest_call(session, logical_call_id)
        if existing is not None:
            if existing.prompt_hash != prompt_hash or existing.context_hash != context_hash:
                raise CallConflict(
                    "same logical call id with different inputs; refusing to merge (§5)"
                )
            stored_config = (existing.sampling or {}).get("config_hash")
            if stored_config and stored_config != config_hash:
                # 同键换了模型或采样参数：不是同一次调用，不得复用也不得合并。
                raise CallConflict(
                    "same logical call id with different model/sampling; refusing to merge (§5)"
                )""",
)

sub(
    """                model=model_hint,
                prompt_hash=prompt_hash,
                context_hash=context_hash,
                status=CALL_STATUS_RESERVED,""",
    """                model=model_hint,
                prompt_hash=prompt_hash,
                context_hash=context_hash,
                sampling={
                    "model": model_hint,
                    "temperature": (sampling or {}).get("temperature", 0),
                    "config_hash": config_hash,
                },
                status=CALL_STATUS_RESERVED,""",
)

# --- 5) 租约有效性校验 ---
sub(
    """def lease_is_free(run: ChapterRunModel, *, now: datetime | None = None) -> bool:""",
    '''def lease_is_valid(
    run: ChapterRunModel,
    *,
    owner: str,
    token: int,
    now: datetime | None = None,
) -> bool:
    """租约是否仍然属于当前持有者（P0-4）。

    模型调用在事务外进行，调用期间别的 worker 可能已接管：写回结果前必须
    复核 owner、fencing token 与有效期，三者缺一不可。
    """
    if int(token or 0) <= 0:
        return False
    if run.lease_owner != owner:
        return False
    if int(run.fencing_token or 0) != int(token):
        return False
    moment = now or datetime.now(UTC)
    expires = run.lease_expires_at
    if expires is None:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires > moment


def require_run_lease(
    run: ChapterRunModel, *, owner: str, token: int, now: datetime | None = None
) -> None:
    """租约失效即拒绝写入：旧 worker 不得覆盖新 worker 的方向。"""
    if not lease_is_valid(run, owner=owner, token=token, now=now):
        raise StaleLease(
            f"chapter run lease lost: owner={run.lease_owner} token={run.fencing_token}"
        )


def lease_is_free(run: ChapterRunModel, *, now: datetime | None = None) -> bool:''',
)

# --- 6) 导出 ---
sub(
    '    "CallResult",\n',
    '    "CallResult",\n    "StaleLease",\n',
)
sub(
    '    "lease_is_free",\n',
    '    "lease_is_free",\n    "lease_is_valid",\n    "require_run_lease",\n',
)
sub(
    '    "logical_call_key",\n',
    '    "config_fingerprint",\n    "logical_call_key",\n',
)

prod.write_text(text, encoding="utf-8")
print("patched", prod)
