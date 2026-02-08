"""Signal processor worker - evaluates intent signals on a schedule.

This worker runs every 15 minutes and evaluates all active signals
across all organizations.
"""

from typing import Any

import structlog

from src.db.pool import get_pool
from src.db.queries.signals import get_all_orgs_with_active_signals
from src.services.signal_evaluator import evaluate_signals_for_org

log = structlog.get_logger()


async def process_signals(ctx: dict[str, Any]) -> None:
    """Background job: evaluate all active signals for all organizations.

    This job is scheduled to run every 15 minutes via arq cron.
    """
    log.info("signal_processor_started")

    pool = await get_pool()

    try:
        # Get all orgs with active signals
        orgs = await get_all_orgs_with_active_signals(pool)

        total_fired = 0
        processed_orgs = 0

        for org in orgs:
            org_id = org["id"]
            try:
                fired_count = await evaluate_signals_for_org(org_id)
                total_fired += fired_count
                processed_orgs += 1

                log.info(
                    "org_signals_evaluated",
                    org_id=str(org_id),
                    fired_count=fired_count,
                )

            except Exception as e:
                log.error(
                    "org_signal_evaluation_error",
                    org_id=str(org_id),
                    error=str(e),
                    exc_info=True,
                )

        log.info(
            "signal_processor_completed",
            processed_orgs=processed_orgs,
            total_signals_fired=total_fired,
        )

    except Exception as e:
        log.error("signal_processor_error", error=str(e), exc_info=True)
