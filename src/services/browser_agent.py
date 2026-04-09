"""AI browser agent — autonomous multi-step web navigation.

Wraps browser-use to give an LLM control of a Playwright browser.
The agent can navigate pages, click, fill forms, paginate, and extract
data across multiple steps without manual selector definitions.

Requires:
    pip install browser-use langchain-openai
    OPENROUTER_API_KEY must be set in settings.
"""

from __future__ import annotations

import structlog

log = structlog.get_logger()


async def run_browser_task(
    task: str,
    start_url: str | None = None,
    max_steps: int = 20,
    org_id: str | None = None,
) -> dict:
    """Run an autonomous browser agent for a web task.

    The agent uses an LLM to decide what to do at each step (navigate,
    click, type, scroll, extract, finish) and executes those actions via
    Playwright. Stops when the task is complete or max_steps is reached.

    Args:
        task: Natural language task description.
        start_url: Optional URL to navigate to before starting.
        max_steps: Maximum browser steps (actions) before stopping.

    Returns:
        {
            success: bool,
            result: str,           # Final extracted result or summary
            steps_taken: int,
            urls_visited: list[str],
            error: str | None,
        }
    """
    from src.services.llm_extractor import get_openrouter_config

    try:
        api_key, model_name = await get_openrouter_config(org_id)
    except ValueError as e:
        return {
            "success": False,
            "result": "",
            "steps_taken": 0,
            "urls_visited": [],
            "error": str(e),
        }

    try:
        from browser_use import Agent
        from langchain_openai import ChatOpenAI
    except ImportError:
        return {
            "success": False,
            "result": "",
            "steps_taken": 0,
            "urls_visited": [],
            "error": (
                "browser-use not installed. "
                "Run: pip install browser-use langchain-openai"
            ),
        }

    llm = ChatOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        model_name=model_name,
        temperature=0.0,
    )

    full_task = f"Navigate to {start_url} and then: {task}" if start_url else task

    log.info(
        "browser_agent_start",
        task_preview=task[:100],
        start_url=start_url,
        max_steps=max_steps,
    )

    try:
        agent = Agent(task=full_task, llm=llm, max_steps=max_steps)
        history = await agent.run()

        final_result = ""
        urls_visited: list[str] = []
        steps_taken = 0

        # Extract results from history object
        # browser-use AgentHistory API
        if hasattr(history, "final_result"):
            final_result = history.final_result() or ""
        if hasattr(history, "model_actions"):
            actions = history.model_actions()
            steps_taken = len(actions)
            urls_visited = list({
                a.url for a in actions if hasattr(a, "url") and a.url
            })
        elif hasattr(history, "history"):
            steps_taken = len(history.history)

        log.info(
            "browser_agent_done",
            steps_taken=steps_taken,
            result_preview=final_result[:100],
        )

        return {
            "success": True,
            "result": final_result,
            "steps_taken": steps_taken,
            "urls_visited": urls_visited,
            "error": None,
        }

    except Exception as e:
        log.error("browser_agent_error", error=str(e), task_preview=task[:100])
        return {
            "success": False,
            "result": "",
            "steps_taken": 0,
            "urls_visited": [],
            "error": str(e),
        }
