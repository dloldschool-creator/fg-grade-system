"""Test tiers and database resilience, shared by every test file.

Why this exists (2026-10-15). The suite ran ~15 minutes against the live
Supabase database, ~85 ms away, and two things followed:

* **It was slow for avoidable reasons.** Two tests render the Parent Notices
  page once per section (~90 round-trip-heavy renders): 400 s of a 940 s
  run, wanted before a deploy and not after every edit.
* **It was unreliable for a reason unrelated to the code.** One run failed
  with `failed to resolve host '...pooler.supabase.com'` — a DNS blip —
  and reported it as 2 failures and 6 errors indistinguishable from bugs.

So:

* ``db`` marks a test that talks to the database; ``pytest -m "not db"`` is
  the offline tier, which needs no network at all.
* ``slow`` marks an exhaustive sweep; it is skipped unless ``--runslow``
  (the ``full`` mode of scripts/run_tests.py passes it).
* A transient failure *opening* a connection is retried a few times with
  backoff — only connection setup, never a query, so a real error is never
  retried into a pass.
* If the database is unreachable when ``db`` tests were selected, the run
  stops once with one clear message rather than failing dozens of tests.
"""

import os
import re
import time
from pathlib import Path

import pytest

# A test file is a database test if it opens a session, imports the engine,
# or renders a page through AppTest (pages open their own). Over-marking
# only costs the offline tier a test; under-marking would fail it when the
# network is down — and tests/test_offline_tier.py checks exactly that.
_DB_HINT = re.compile(r"SessionLocal|get_session|app\.database|AppTest")

# Connection-setup errors worth retrying. Authentication failures are not
# listed on purpose: a wrong password should fail at once.
_TRANSIENT = re.compile(
    r"resolve host|name resolution|timeout|timed out|could not connect|"
    r"connection refused|connection reset|server closed the connection|"
    r"network is unreachable",
    re.IGNORECASE,
)

_RETRIES = 4  # attempts, with 1 s, 2 s, 4 s between them


def pytest_addoption(parser):
    parser.addoption(
        "--runslow",
        action="store_true",
        default=False,
        help="also run tests marked slow (exhaustive per-section sweeps)",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "db: talks to the database (needs the network)")
    config.addinivalue_line("markers", "slow: exhaustive sweep, run with --runslow")
    _install_connect_retry()


def _install_connect_retry() -> None:
    """Retry transient failures opening a connection. Quietly does nothing
    when the database isn't configured — an offline run must still work."""
    if os.environ.get("FG_TEST_NO_CONNECT_RETRY"):
        return
    try:
        from sqlalchemy import event

        from app.database import engine
    except Exception:  # no DB_* settings: offline tier only
        return

    @event.listens_for(engine, "do_connect")
    def _connect_with_retry(dialect, conn_rec, cargs, cparams):
        for attempt in range(_RETRIES):
            try:
                return dialect.connect(*cargs, **cparams)
            except dialect.dbapi.OperationalError as exc:
                if attempt == _RETRIES - 1 or not _TRANSIENT.search(str(exc)):
                    raise
                time.sleep(2**attempt)


_source_cache: dict[str, str] = {}


def _is_db_file(path: Path) -> bool:
    key = str(path)
    if key not in _source_cache:
        _source_cache[key] = path.read_text(encoding="utf-8", errors="ignore")
    return bool(_DB_HINT.search(_source_cache[key]))


def pytest_collection_modifyitems(config, items):
    run_slow = config.getoption("--runslow")
    skip_slow = pytest.mark.skip(reason="slow sweep: pass --runslow (or run scripts/run_tests.py full)")
    for item in items:
        if _is_db_file(Path(str(item.fspath))):
            item.add_marker(pytest.mark.db)
        if "slow" in item.keywords and not run_slow:
            item.add_marker(skip_slow)


def pytest_collection_finish(session):
    """Stop once, clearly, if database tests are selected and the database
    can't be reached — instead of dozens of failures that read as bugs."""
    wanted = [i for i in session.items if "db" in i.keywords and not _is_skipped(i)]
    if not wanted:
        return
    try:
        _check_database_reachable()
    except Exception as exc:
        first_line = (str(exc).splitlines() or [""])[0]
        pytest.exit(
            f"The database is unreachable, and {len(wanted)} selected test(s) need it:\n"
            f"  {type(exc).__name__}: {first_line}\n"
            'Nothing was run. Check the network, or run the offline tier: pytest -m "not db"',
            returncode=3,
        )


def _check_database_reachable() -> None:
    """A direct connection with a short timeout, retried like the engine's.
    Not the app engine: it sets no connect timeout (right for the app), so
    against a dead address it would hang for minutes before giving up."""
    import psycopg

    from app.database import build_database_url

    url = build_database_url()
    for attempt in range(_RETRIES):
        try:
            psycopg.connect(
                host=url.host,
                port=url.port,
                dbname=url.database,
                user=url.username,
                password=url.password,
                connect_timeout=8,
            ).close()
            return
        except psycopg.OperationalError as exc:
            if attempt == _RETRIES - 1 or not _TRANSIENT.search(str(exc)):
                raise
            time.sleep(2**attempt)


def _is_skipped(item) -> bool:
    return any(m.name == "skip" for m in item.iter_markers())
