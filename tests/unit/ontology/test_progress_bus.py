import asyncio

from sales_agent.ontology.progress import JobProgressBus


async def test_subscribe_then_publish_receives_event():
    bus = JobProgressBus()
    q = bus.subscribe("j1")
    await bus.publish("j1", {"stage": "parsed"})
    event = await asyncio.wait_for(q.get(), timeout=0.5)
    assert event == {"stage": "parsed"}


async def test_multiple_subscribers_both_get_event():
    bus = JobProgressBus()
    q1 = bus.subscribe("j1")
    q2 = bus.subscribe("j1")
    await bus.publish("j1", {"stage": "extracting_entities"})
    e1 = await asyncio.wait_for(q1.get(), timeout=0.5)
    e2 = await asyncio.wait_for(q2.get(), timeout=0.5)
    assert e1 == e2 == {"stage": "extracting_entities"}


async def test_different_jobs_isolated():
    bus = JobProgressBus()
    q1 = bus.subscribe("j1")
    q2 = bus.subscribe("j2")
    await bus.publish("j1", {"stage": "a"})
    assert q2.empty()  # j2 subscriber should not get j1's event
    e1 = await asyncio.wait_for(q1.get(), timeout=0.5)
    assert e1 == {"stage": "a"}


async def test_remove_cleans_up():
    bus = JobProgressBus()
    q = bus.subscribe("j1")
    bus.remove("j1")
    await bus.publish("j1", {"stage": "x"})
    assert q.empty()
