from regent.runtime.dispatcher import claim_statement
from sqlalchemy.dialects import postgresql


def test_claim_uses_skip_locked_and_database_time() -> None:
    sql = str(
        claim_statement(10).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "now()" in sql
    assert "LIMIT 10" in sql
    assert "outbox_events.status IN ('PENDING', 'FAILED')" in sql
