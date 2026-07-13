from ai_platform.common.schemas import ChatMessage
from ai_platform.memory.in_memory import InMemoryStore


async def test_load_returns_empty_list_for_unknown_conversation():
    store = InMemoryStore()

    assert await store.load("does-not-exist") == []


async def test_append_then_load_returns_the_appended_messages():
    store = InMemoryStore()
    messages = [ChatMessage(role="user", content="hi"), ChatMessage(role="assistant", content="hello")]

    await store.append("conv-1", messages)

    assert await store.load("conv-1") == messages


async def test_multiple_appends_accumulate_in_order():
    store = InMemoryStore()

    await store.append("conv-1", [ChatMessage(role="user", content="first")])
    await store.append("conv-1", [ChatMessage(role="assistant", content="second")])

    loaded = await store.load("conv-1")
    assert [m.content for m in loaded] == ["first", "second"]


async def test_conversations_are_isolated_by_id():
    store = InMemoryStore()

    await store.append("conv-1", [ChatMessage(role="user", content="conv-1 message")])
    await store.append("conv-2", [ChatMessage(role="user", content="conv-2 message")])

    assert await store.load("conv-1") != await store.load("conv-2")


async def test_load_returns_a_copy_not_the_internal_list():
    store = InMemoryStore()
    await store.append("conv-1", [ChatMessage(role="user", content="hi")])

    loaded = await store.load("conv-1")
    loaded.append(ChatMessage(role="user", content="mutated"))

    assert len(await store.load("conv-1")) == 1
