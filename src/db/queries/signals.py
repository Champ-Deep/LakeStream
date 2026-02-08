"""Database queries for signals and signal executions."""

from typing import Any
from uuid import UUID

from asyncpg import Pool

from src.models.signals import Signal, SignalExecution, SignalType


# ============================================================================
# Signal Type Queries
# ============================================================================


async def get_signal_types(pool: Pool, category: str | None = None) -> list[SignalType]:
    """Get all signal types, optionally filtered by category."""
    if category:
        query = "SELECT * FROM signal_types WHERE category = $1 AND enabled = TRUE ORDER BY name"
        rows = await pool.fetch(query, category)
    else:
        query = "SELECT * FROM signal_types WHERE enabled = TRUE ORDER BY category, name"
        rows = await pool.fetch(query)

    return [SignalType(**dict(row)) for row in rows]


async def get_signal_type(pool: Pool, type_id: str) -> SignalType | None:
    """Get a signal type by ID."""
    query = "SELECT * FROM signal_types WHERE id = $1"
    row = await pool.fetchrow(query, type_id)
    return SignalType(**dict(row)) if row else None


# ============================================================================
# Signal Queries
# ============================================================================


async def create_signal(
    pool: Pool,
    org_id: UUID,
    name: str,
    trigger_config: dict[str, Any],
    action_config: dict[str, Any],
    created_by: UUID,
    description: str | None = None,
    condition_config: dict[str, Any] | None = None,
    is_active: bool = True,
) -> Signal:
    """Create a new signal."""
    query = """
        INSERT INTO signals (
            org_id, name, description, is_active,
            trigger_config, condition_config, action_config,
            created_by
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING *
    """
    row = await pool.fetchrow(
        query,
        org_id,
        name,
        description,
        is_active,
        trigger_config,
        condition_config,
        action_config,
        created_by,
    )
    assert row is not None
    return Signal(**dict(row))


async def get_signals_by_org(
    pool: Pool, org_id: UUID, is_active: bool | None = None
) -> list[Signal]:
    """Get all signals for an organization."""
    if is_active is not None:
        query = """
            SELECT * FROM signals
            WHERE org_id = $1 AND is_active = $2
            ORDER BY created_at DESC
        """
        rows = await pool.fetch(query, org_id, is_active)
    else:
        query = """
            SELECT * FROM signals
            WHERE org_id = $1
            ORDER BY created_at DESC
        """
        rows = await pool.fetch(query, org_id)

    return [Signal(**dict(row)) for row in rows]


async def get_signal(pool: Pool, signal_id: UUID) -> Signal | None:
    """Get a signal by ID."""
    query = "SELECT * FROM signals WHERE id = $1"
    row = await pool.fetchrow(query, signal_id)
    return Signal(**dict(row)) if row else None


async def get_active_signals(pool: Pool, org_id: UUID | None = None) -> list[Signal]:
    """Get all active signals, optionally filtered by org."""
    if org_id:
        query = """
            SELECT * FROM signals
            WHERE is_active = TRUE AND org_id = $1
            ORDER BY created_at DESC
        """
        rows = await pool.fetch(query, org_id)
    else:
        query = """
            SELECT * FROM signals
            WHERE is_active = TRUE
            ORDER BY created_at DESC
        """
        rows = await pool.fetch(query)

    return [Signal(**dict(row)) for row in rows]


async def update_signal(
    pool: Pool,
    signal_id: UUID,
    name: str | None = None,
    description: str | None = None,
    is_active: bool | None = None,
    trigger_config: dict[str, Any] | None = None,
    condition_config: dict[str, Any] | None = None,
    action_config: dict[str, Any] | None = None,
) -> Signal | None:
    """Update a signal."""
    updates = []
    values: list[Any] = []
    param_num = 1

    if name is not None:
        updates.append(f"name = ${param_num}")
        values.append(name)
        param_num += 1

    if description is not None:
        updates.append(f"description = ${param_num}")
        values.append(description)
        param_num += 1

    if is_active is not None:
        updates.append(f"is_active = ${param_num}")
        values.append(is_active)
        param_num += 1

    if trigger_config is not None:
        updates.append(f"trigger_config = ${param_num}")
        values.append(trigger_config)
        param_num += 1

    if condition_config is not None:
        updates.append(f"condition_config = ${param_num}")
        values.append(condition_config)
        param_num += 1

    if action_config is not None:
        updates.append(f"action_config = ${param_num}")
        values.append(action_config)
        param_num += 1

    if not updates:
        return await get_signal(pool, signal_id)

    updates.append(f"updated_at = NOW()")

    query = f"""
        UPDATE signals
        SET {', '.join(updates)}
        WHERE id = ${param_num}
        RETURNING *
    """
    values.append(signal_id)

    row = await pool.fetchrow(query, *values)
    return Signal(**dict(row)) if row else None


async def delete_signal(pool: Pool, signal_id: UUID) -> bool:
    """Delete a signal."""
    query = "DELETE FROM signals WHERE id = $1 RETURNING id"
    result = await pool.fetchrow(query, signal_id)
    return result is not None


async def increment_signal_fire_count(pool: Pool, signal_id: UUID) -> None:
    """Increment the fire count for a signal."""
    query = """
        UPDATE signals
        SET fire_count = fire_count + 1,
            last_fired_at = NOW()
        WHERE id = $1
    """
    await pool.execute(query, signal_id)


# ============================================================================
# Signal Execution Queries
# ============================================================================


async def create_signal_execution(
    pool: Pool,
    signal_id: UUID,
    org_id: UUID,
    trigger_data: dict[str, Any],
    action_type: str,
    action_status: str,
    action_response: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> SignalExecution:
    """Create a signal execution log entry."""
    query = """
        INSERT INTO signal_executions (
            signal_id, org_id, trigger_data,
            action_type, action_status, action_response, error_message
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING *
    """
    row = await pool.fetchrow(
        query,
        signal_id,
        org_id,
        trigger_data,
        action_type,
        action_status,
        action_response,
        error_message,
    )
    assert row is not None
    return SignalExecution(**dict(row))


async def get_signal_execution_history(
    pool: Pool, signal_id: UUID, limit: int = 100
) -> list[SignalExecution]:
    """Get execution history for a signal."""
    query = """
        SELECT * FROM signal_executions
        WHERE signal_id = $1
        ORDER BY matched_at DESC
        LIMIT $2
    """
    rows = await pool.fetch(query, signal_id, limit)
    return [SignalExecution(**dict(row)) for row in rows]


async def get_signal_execution_stats(
    pool: Pool, signal_id: UUID
) -> dict[str, Any]:
    """Get execution statistics for a signal."""
    query = """
        SELECT
            COUNT(*) as total_executions,
            COUNT(*) FILTER (WHERE action_status = 'success') as successful,
            COUNT(*) FILTER (WHERE action_status = 'failed') as failed,
            COUNT(*) FILTER (WHERE action_status = 'pending') as pending,
            MAX(matched_at) as last_execution
        FROM signal_executions
        WHERE signal_id = $1
    """
    row = await pool.fetchrow(query, signal_id)
    return dict(row) if row else {}


# ============================================================================
# Bulk Operations
# ============================================================================


async def get_all_orgs_with_active_signals(pool: Pool) -> list[dict[str, Any]]:
    """Get all organizations that have active signals."""
    query = """
        SELECT DISTINCT org_id FROM signals WHERE is_active = TRUE
    """
    rows = await pool.fetch(query)
    return [{"id": row["org_id"]} for row in rows]
