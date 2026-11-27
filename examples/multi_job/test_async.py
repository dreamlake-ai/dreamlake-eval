import asyncio
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def sample():
    await asyncio.sleep(0)
    yield 2
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_async_trial(sample, eval_trial):
    await asyncio.sleep(0)
    eval_trial.metric("reward", sample, step=0)
    assert sample == 2
