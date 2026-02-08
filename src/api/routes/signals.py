"""API routes for intent signals."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.middleware.auth import get_current_user
from src.db.pool import get_pool
from src.db.queries.signals import (
    create_signal,
    delete_signal,
    get_signal,
    get_signal_execution_history,
    get_signal_execution_stats,
    get_signal_types,
    get_signals_by_org,
    update_signal,
)
from src.models.signals import (
    CreateSignalRequest,
    Signal,
    SignalExecutionListResponse,
    SignalTestResponse,
    SignalType,
    UpdateSignalRequest,
)
from src.services.signal_evaluator import evaluate_signal

router = APIRouter(prefix="/signals", tags=["signals"])


# ============================================================================
# Signal Type Routes
# ============================================================================


@router.get("/types", response_model=list[SignalType])
async def list_signal_types(category: str | None = None):
    """List available signal types."""
    pool = await get_pool()
    return await get_signal_types(pool, category)


# ============================================================================
# Signal CRUD Routes
# ============================================================================


@router.post("/", response_model=Signal, status_code=status.HTTP_201_CREATED)
async def create_new_signal(
    request: CreateSignalRequest, user: dict = Depends(get_current_user)
):
    """Create a new intent signal."""
    pool = await get_pool()

    # Set RLS context
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.current_org_id = '{user['org_id']}'")

        signal = await create_signal(
            pool,
            org_id=UUID(user["org_id"]),
            name=request.name,
            description=request.description,
            is_active=request.is_active,
            trigger_config=request.trigger_config.model_dump(),
            condition_config=(
                request.condition_config.model_dump() if request.condition_config else None
            ),
            action_config=request.action_config.model_dump(),
            created_by=UUID(user["user_id"]),
        )

        return signal


@router.get("/", response_model=list[Signal])
async def list_signals(
    is_active: bool | None = None, user: dict = Depends(get_current_user)
):
    """List all signals for the current organization."""
    pool = await get_pool()

    # Set RLS context
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.current_org_id = '{user['org_id']}'")

        signals = await get_signals_by_org(pool, UUID(user["org_id"]), is_active)
        return signals


@router.get("/{signal_id}", response_model=Signal)
async def get_signal_by_id(signal_id: UUID, user: dict = Depends(get_current_user)):
    """Get a signal by ID."""
    pool = await get_pool()

    # Set RLS context
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.current_org_id = '{user['org_id']}'")

        signal = await get_signal(pool, signal_id)
        if not signal:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Signal not found"
            )

        # Verify ownership
        if str(signal.org_id) != user["org_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to access this signal",
            )

        return signal


@router.patch("/{signal_id}", response_model=Signal)
async def update_signal_by_id(
    signal_id: UUID, request: UpdateSignalRequest, user: dict = Depends(get_current_user)
):
    """Update a signal."""
    pool = await get_pool()

    # Set RLS context
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.current_org_id = '{user['org_id']}'")

        # Verify signal exists and user has access
        existing = await get_signal(pool, signal_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Signal not found"
            )

        if str(existing.org_id) != user["org_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to update this signal",
            )

        # Update signal
        updated = await update_signal(
            pool,
            signal_id,
            name=request.name,
            description=request.description,
            is_active=request.is_active,
            trigger_config=(
                request.trigger_config.model_dump() if request.trigger_config else None
            ),
            condition_config=(
                request.condition_config.model_dump() if request.condition_config else None
            ),
            action_config=(
                request.action_config.model_dump() if request.action_config else None
            ),
        )

        if not updated:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update signal",
            )

        return updated


@router.delete("/{signal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_signal_by_id(signal_id: UUID, user: dict = Depends(get_current_user)):
    """Delete a signal."""
    pool = await get_pool()

    # Set RLS context
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.current_org_id = '{user['org_id']}'")

        # Verify signal exists and user has access
        existing = await get_signal(pool, signal_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Signal not found"
            )

        if str(existing.org_id) != user["org_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to delete this signal",
            )

        # Delete signal
        success = await delete_signal(pool, signal_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete signal",
            )


# ============================================================================
# Signal Testing and Execution History
# ============================================================================


@router.post("/{signal_id}/test", response_model=SignalTestResponse)
async def test_signal(signal_id: UUID, user: dict = Depends(get_current_user)):
    """Test a signal (dry run) to see if it would fire."""
    pool = await get_pool()

    # Set RLS context
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.current_org_id = '{user['org_id']}'")

        # Verify signal exists and user has access
        signal = await get_signal(pool, signal_id)
        if not signal:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Signal not found"
            )

        if str(signal.org_id) != user["org_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to test this signal",
            )

        # Evaluate signal
        matched_data = await evaluate_signal(pool, signal, UUID(user["org_id"]))

        return SignalTestResponse(
            signal_id=signal_id,
            would_fire=matched_data is not None,
            matched_data=matched_data,
            match_count=matched_data.get("match_count", 0) if matched_data else 0,
            message=(
                f"Signal would fire with {matched_data.get('match_count', 0)} matches"
                if matched_data
                else "Signal would not fire - no matches found"
            ),
        )


@router.get("/{signal_id}/executions", response_model=SignalExecutionListResponse)
async def get_signal_executions(
    signal_id: UUID, limit: int = 100, user: dict = Depends(get_current_user)
):
    """Get execution history for a signal."""
    pool = await get_pool()

    # Set RLS context
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.current_org_id = '{user['org_id']}'")

        # Verify signal exists and user has access
        signal = await get_signal(pool, signal_id)
        if not signal:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Signal not found"
            )

        if str(signal.org_id) != user["org_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view this signal's executions",
            )

        # Get execution history
        executions = await get_signal_execution_history(pool, signal_id, limit)
        stats = await get_signal_execution_stats(pool, signal_id)

        return SignalExecutionListResponse(
            signal_id=signal_id,
            total_count=stats.get("total_executions", 0),
            executions=executions,
        )
