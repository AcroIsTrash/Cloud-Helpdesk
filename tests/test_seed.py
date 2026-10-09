import threading

from sqlalchemy import func, select

from app.db import tickets, users
from app.routing import KeywordRouter
from app.seed import SEED_USERS, seed_on_startup


def test_tasks_starting_together_seed_once(engine, empty_db):
    # ECS starts 2-4 tasks at once; each seeds on startup. Exactly one must win.
    starters = 4
    barrier = threading.Barrier(starters)
    errors: list[BaseException] = []

    def start_task():
        try:
            with engine.connect() as c:
                barrier.wait()
                seed_on_startup(c, KeywordRouter(), demo=True)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=start_task) for _ in range(starters)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(users)) == len(SEED_USERS)
        assert c.scalar(select(func.count()).select_from(tickets)) == 5
