import pytest
import sales_agent.graph.checkpoint_runtime as runtime


@pytest.mark.asyncio
async def test_initialize_opens_pool_runs_migrations_and_caches(monkeypatch):
    events = []

    class FakePool:
        def __init__(self, conninfo, min_size, max_size, open):
            assert open is False
            events.append(("created", conninfo, min_size, max_size))

        async def open(self):
            events.append("opened")

        async def close(self):
            events.append("closed")

    class FakeSaver:
        MIGRATIONS = ["CREATE TABLE IF NOT EXISTS checkpoint_migrations (v INTEGER PRIMARY KEY)"]

        def __init__(self, conn):
            self.conn = conn

    # Mock _run_migrations to avoid needing a real DB connection.
    async def fake_run_migrations(pool, saver):
        events.append("migrations_run")

    monkeypatch.setattr(runtime, "_run_migrations", fake_run_migrations)
    monkeypatch.setattr(runtime, "AsyncConnectionPool", FakePool)
    monkeypatch.setattr(runtime, "AsyncPostgresSaver", FakeSaver)

    saver = await runtime.initialize_production_checkpointer(
        "postgresql+asyncpg://user:pass@db/app"
    )
    assert saver is runtime.get_production_checkpointer()
    assert runtime.production_checkpoint_ready() is True
    assert events == [
        ("created", "postgresql://user:pass@db/app", 1, 5),
        "opened",
        "migrations_run",
    ]

    await runtime.close_production_checkpointer()
    assert events[-1] == "closed"
    assert runtime.production_checkpoint_ready() is False


def test_access_before_initialize_fails_closed():
    with pytest.raises(runtime.CheckpointUnavailableError):
        runtime.get_production_checkpointer()


@pytest.mark.asyncio
async def test_migrations_failure_leaves_readiness_false_and_closes_pool(monkeypatch):
    events = []

    class FakePool:
        def __init__(self, conninfo, min_size, max_size, open):
            assert open is False

        async def open(self):
            events.append("opened")

        async def close(self):
            events.append("closed")

    class FakeSaver:
        MIGRATIONS = ["CREATE TABLE IF NOT EXISTS checkpoint_migrations (v INTEGER PRIMARY KEY)"]

        def __init__(self, conn):
            pass

    async def fake_run_migrations(pool, saver):
        raise RuntimeError("migrations exploded")

    monkeypatch.setattr(runtime, "_run_migrations", fake_run_migrations)
    monkeypatch.setattr(runtime, "AsyncConnectionPool", FakePool)
    monkeypatch.setattr(runtime, "AsyncPostgresSaver", FakeSaver)

    with pytest.raises(runtime.CheckpointUnavailableError):
        await runtime.initialize_production_checkpointer(
            "postgresql+asyncpg://user:pass@db/app"
        )

    # Failure must not cache a saver, and the partially opened pool must be closed.
    assert runtime.production_checkpoint_ready() is False
    assert events == ["opened", "closed"]
    with pytest.raises(runtime.CheckpointUnavailableError):
        runtime.get_production_checkpointer()
