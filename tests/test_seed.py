import threading

from sqlalchemy import func, select

from app import seed
from app.db import comments, events, tickets, users
from app.routing import KeywordRouter
from app.seed import SEED_USERS


def counts(engine) -> list[int]:
    with engine.connect() as c:
        return [
            c.scalar(select(func.count()).select_from(t))
            for t in (users, tickets, comments, events)
        ]


def test_running_the_seed_command_twice_adds_nothing_the_second_time(engine, empty_db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", empty_db)
    seed.main()
    first = counts(engine)
    seed.main()
    assert counts(engine) == first
    assert first[:2] == [len(SEED_USERS), 5]


def test_seeds_running_together_seed_once(engine, empty_db):
    # Two bring-ups racing (a retried one-off task, say): exactly one must win.
    starters = 4
    barrier = threading.Barrier(starters)
    errors: list[BaseException] = []

    def run_seed():
        try:
            with engine.connect() as c:
                barrier.wait()
                seed.seed(c, KeywordRouter(), demo=True)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run_seed) for _ in range(starters)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert counts(engine)[:2] == [len(SEED_USERS), 5]
