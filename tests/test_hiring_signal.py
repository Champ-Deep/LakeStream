"""The hiring signal, from dead code to a firing signal.

Before 2026-07-30 `check_hiring_spike_signal` filtered on
`data_type = 'job_posting'` — a value with no member in the DataType enum and
no writer anywhere in the codebase. The string appeared in exactly one place:
that WHERE clause. The signal could not fire, ever.

These tests pin the three things that had to be true for it to work:
  1. the enum value exists,
  2. something writes it, in the shape the query reads,
  3. the query's own filters do what their configuration says.

They deliberately avoid a live database: the persistence layer is exercised
through a fake pool that records what it was asked to write, and the SQL is
asserted on structurally. A Postgres-backed integration test is worth adding
later, but it would not have caught any of the defects fixed here — every one
of them was visible in the source.
"""
from __future__ import annotations

import re
from uuid import uuid4

import pytest

from src.models.scraped_data import DataType
from src.services.job_boards import (
    BoardResult,
    JobPosting,
    persist_board_results,
    persist_postings,
    posting_to_record,
)


def _posting(**kw) -> JobPosting:
    base = dict(
        provider="greenhouse",
        board_token="acme",
        external_id="1",
        title="Senior Backend Engineer",
        url="https://boards.greenhouse.io/acme/jobs/1",
        department="Engineering",
        location="Bangalore",
        remote=False,
        posted_at="2026-07-28T00:00:00Z",
        description="We are hiring." * 500,
    )
    base.update(kw)
    return JobPosting(**base)


class _FakePool:
    """Captures what would have been written."""

    def __init__(self) -> None:
        self.executed: list[tuple] = []

    async def executemany(self, _sql, values):
        self.executed.extend(values)

    async def execute(self, *_a, **_kw):
        return None


# --- 1. the enum value exists ---------------------------------------------


def test_job_posting_is_a_real_data_type():
    """The root cause. Without this member nothing could ever write the value
    the hiring query filters on."""
    assert DataType.JOB_POSTING == "job_posting"
    assert "job_posting" in {member.value for member in DataType}


# --- 2. something writes it, in the shape the query reads ------------------


def test_record_uses_the_data_type_the_signal_queries():
    record = posting_to_record(_posting(), job_id=uuid4(), domain="acme.com")
    assert record["data_type"] == "job_posting"


def test_record_carries_department_because_the_signal_filters_on_it():
    """check_hiring_spike_signal reads metadata->>'department'. If the writer
    omitted it, a department-scoped signal would silently match nothing."""
    record = posting_to_record(_posting(department="Engineering"),
                               job_id=uuid4(), domain="acme.com")
    assert record["metadata"]["department"] == "Engineering"


def test_record_url_is_set_so_reruns_upsert_rather_than_duplicate():
    """scraped_data upserts on (domain, url, data_type). A null url would make
    every re-fetch insert a new row and inflate the open-role count until any
    threshold tripped."""
    record = posting_to_record(_posting(), job_id=uuid4(), domain="acme.com")
    assert record["url"]


def test_description_is_truncated():
    """A full JD per row bloats the JSONB column, and the signal reads counts
    and departments — never the prose."""
    record = posting_to_record(_posting(), job_id=uuid4(), domain="acme.com")
    assert len(record["metadata"]["description_excerpt"]) <= 1000


@pytest.mark.asyncio
async def test_persist_writes_one_row_per_posting():
    pool = _FakePool()
    result = BoardResult(
        provider="greenhouse", board_token="acme",
        postings=[_posting(external_id="1", url="https://x/1"),
                  _posting(external_id="2", url="https://x/2")],
    )
    written = await persist_postings(
        pool, result, domain="acme.com", job_id=uuid4()
    )
    assert written == 2
    assert len(pool.executed) == 2


@pytest.mark.asyncio
async def test_a_failed_fetch_writes_nothing():
    """An empty write is not the same as 'this company has no open roles'.
    Recording a failed fetch as zero postings would read as a hiring slowdown."""
    pool = _FakePool()
    result = BoardResult(
        provider="greenhouse", board_token="acme",
        postings=[], error="board returned 404",
    )
    assert await persist_postings(
        pool, result, domain="acme.com", job_id=uuid4()
    ) == 0
    assert pool.executed == []


@pytest.mark.asyncio
async def test_one_company_failing_does_not_lose_the_batch():
    class _Flaky(_FakePool):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def executemany(self, sql, values):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient")
            await super().executemany(sql, values)

    pool = _Flaky()
    results = {
        "a.com": BoardResult(provider="greenhouse", board_token="a",
                             postings=[_posting(url="https://a/1")]),
        "b.com": BoardResult(provider="lever", board_token="b",
                             postings=[_posting(url="https://b/1")]),
    }
    written = await persist_board_results(pool, results, job_id=uuid4())
    assert written == 1, "the second company should still have been written"


# --- 3. the query filters do what the configuration says -------------------


def _signal_source() -> str:
    from src.services import signal_evaluator

    import inspect

    return inspect.getsource(signal_evaluator)


def _executable_source(func_name: str) -> str:
    """Source of one function with its docstring removed.

    Needed because the docstrings added by this fix quote the buggy expressions
    they replaced. A substring search over raw source matches the changelog and
    reports a regression that is not there — which is how this test failed the
    first time it ran.
    """
    import ast

    tree = ast.parse(_signal_source())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]
            return "\n".join(ast.unparse(stmt) for stmt in body)
    raise AssertionError(f"{func_name} not found in signal_evaluator")


def test_hiring_signal_no_longer_silently_doubles_the_threshold():
    """It passed the threshold doubled, with the comment 'Simplified
    threshold', so a user configuring 3 got 6 and never knew."""
    code = _executable_source("check_hiring_spike_signal")
    assert "spike_threshold * 2" not in code, "threshold is still being doubled"


def test_hiring_signal_passes_the_configured_threshold_through():
    code = _executable_source("check_hiring_spike_signal")
    assert "spike_threshold" in code, "the configured threshold is not used at all"


def test_hiring_signal_actually_uses_the_department_filter():
    """`department` was read, marked `noqa: F841` and never used, so a signal
    scoped to Engineering fired on any hiring at all."""
    src = _signal_source()
    assert "_department = filters.get" not in src
    assert "metadata->>'department'" in src


def test_job_change_signal_compares_against_a_previous_title():
    """It used to select every contact whose CURRENT title matched — detecting
    'a contact exists with this title', not 'someone changed jobs' — so every
    match re-fired on every pass."""
    src = _signal_source()
    assert "previous_job_title" in src
    assert "IS DISTINCT FROM" in src


def test_job_change_signal_ignores_case_only_differences():
    """A re-scrape that only changed capitalisation is not a job change."""
    src = _signal_source()
    assert re.search(r"lower\(metadata->>'previous_job_title'\)", src)


# --- 4. detection -> outreach: the champiq action --------------------------
#
# slack/webhook/email all terminate at a human reading a notification. The
# `champiq` action publishes the canonical `signal.matched` event onto the bus,
# where a trigger.event DAG can enrich, suppression-check and enrol without
# anyone in the loop. That is what makes signal-triggered outreach real rather
# than a report somebody acts on later.


def _signal_obj(**kw):
    from types import SimpleNamespace
    from uuid import uuid4 as _u

    base = dict(id=_u(), name="Hiring spike — Engineering", org_id=_u())
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_champiq_action_requires_a_url():
    """Better to fail loudly than to silently publish nowhere — a signal that
    thinks it fired but reached no one is worse than one that errors."""
    from src.services.signal_evaluator import publish_signal_to_champiq

    with pytest.raises(ValueError):
        await publish_signal_to_champiq(
            _signal_obj(), {"matches": [{"domain": "acme.com"}]}, {}
        )


@pytest.mark.asyncio
async def test_champiq_action_publishes_one_event_per_company(monkeypatch):
    import src.services.signal_evaluator as ev

    sent: list[dict] = []

    class _Resp:
        status_code = 200

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, json=None, headers=None):
            sent.append({"url": url, "body": json})
            return _Resp()

    monkeypatch.setattr(ev.httpx, "AsyncClient", lambda **_kw: _Client())

    result = await ev.publish_signal_to_champiq(
        _signal_obj(),
        {
            "signal_type": "hiring_spike",
            "matches": [
                {"domain": "acme.com", "job_count": 7},
                {"domain": "globex.com", "job_count": 4},
            ],
        },
        {"champiq_url": "https://champiq.test"},
    )

    assert result["published"] == 2
    assert {e["body"]["company_domain"] for e in sent} == {"acme.com", "globex.com"}
    assert all(e["body"]["type"] == "signal.matched" for e in sent)


@pytest.mark.asyncio
async def test_published_event_carries_company_domain_for_lead_correlation(monkeypatch):
    """ChampIQ derives a `co:`-prefixed lead key from company_domain when no
    contact email exists yet, so a pre-contact hiring signal joins the same
    lead journey that later email events land on. Drop this field and the
    signal becomes an orphan event."""
    import src.services.signal_evaluator as ev

    sent: list[dict] = []

    class _Resp:
        status_code = 200

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, json=None, headers=None):
            sent.append(json)
            return _Resp()

    monkeypatch.setattr(ev.httpx, "AsyncClient", lambda **_kw: _Client())

    await ev.publish_signal_to_champiq(
        _signal_obj(),
        {"signal_type": "hiring_spike", "matches": [{"domain": "acme.com", "job_count": 9}]},
        {"champiq_url": "https://champiq.test", "account_name": "acme"},
    )

    body = sent[0]
    assert body["company_domain"] == "acme.com"
    assert body["account_name"] == "acme"
    assert body["evidence"]["job_count"] == 9


@pytest.mark.asyncio
async def test_one_company_failing_does_not_drop_the_rest(monkeypatch):
    import src.services.signal_evaluator as ev

    class _Resp:
        status_code = 200

    class _Client:
        calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, json=None, headers=None):
            _Client.calls += 1
            if _Client.calls == 1:
                raise RuntimeError("connection reset")
            return _Resp()

    monkeypatch.setattr(ev.httpx, "AsyncClient", lambda **_kw: _Client())

    result = await ev.publish_signal_to_champiq(
        _signal_obj(),
        {"matches": [{"domain": "a.com"}, {"domain": "b.com"}]},
        {"champiq_url": "https://champiq.test"},
    )
    assert result["published"] == 1
    assert result["attempted"] == 2


@pytest.mark.asyncio
async def test_matches_without_a_domain_are_skipped(monkeypatch):
    """A match that cannot be attributed to a company cannot be acted on."""
    import src.services.signal_evaluator as ev

    result = await ev.publish_signal_to_champiq(
        _signal_obj(),
        {"matches": [{"job_count": 5}]},
        {"champiq_url": "https://champiq.test"},
    )
    assert result["published"] == 0
