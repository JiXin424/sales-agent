import pytest
import sales_agent.graph.checkpoint_runtime as runtime


@pytest.mark.asyncio
async def test_initialize_opens_pool_runs_setup_and_caches(monkeypatch):
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
        def __init__(self, conn):
            self.conn = conn
        async def setup(self):
            events.append("setup")

    monkeypatch.setattr(runtime, "AsyncConnectionPool", FakePool)
    monkeypatch.setattr(runtime, "AsyncPostgresSaver", FakeSaver)

    saver = await runtime.initialize_production_checkpointer(
        "postgresql+asyncpg://user:pass@db/app"
    )
    assert saver is runtime.get_production_checkpointer()
    assert runtime.production_checkpoint_ready() is True
    assert events == [
        ("created", "postgresql://user:pass@db/app", 1, 5),
        "opened", "setup",
    ]

    await runtime.close_production_checkpointer()
    assert events[-1] == "closed"
    assert runtime.production_checkpoint_ready() is False


def test_access_before_initialize_fails_closed():
    with pytest.raises(runtime.CheckpointUnavailableError):
        runtime.get_production_checkpointer()


@pytest.mark.asyncio
async def test_setup_failure_leaves_readiness_false_and_closes_pool(monkeypatch):
    events = []

    class FakePool:
        def __init__(self, conninfo, min_size, max_size, open):
            assert open is False
        async def open(self):
            events.append("opened")
        async def close(self):
            events.append("closed")

    class FakeSaver:
        def __init__(self, conn):
            pass
        async def setup(self):
            raise RuntimeError("setup exploded")

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
