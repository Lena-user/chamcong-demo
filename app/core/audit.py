from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.employees.models import AuditLog


def write_audit(
    session: AsyncSession, actor_id: int | None, action: str, target_type: str, target_id: int | None
) -> None:
    """Add an audit row to the caller's transaction (committed with it)."""
    session.add(AuditLog(actor_id=actor_id, action=action, target_type=target_type, target_id=target_id))
