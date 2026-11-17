from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Generator, Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("jsonschema")
pytest.importorskip("referencing")
pytest.importorskip("websockets")

from tests.conformance.harness import Bench, HubDouble, start_bench


@pytest.fixture
async def hub() -> AsyncIterator[HubDouble]:
    double = HubDouble()
    await double.start()
    try:
        yield double
    finally:
        await double.stop()


@pytest.fixture
def bench(tmp_path: Path, hub: HubDouble) -> Iterator[Callable[..., Bench]]:
    """Start the bench under test against `hub`; every bench started is
    stopped at the end, and its output is shown when the test failed."""
    started: list[Bench] = []

    def launch(**config: Any) -> Bench:
        b = start_bench(tmp_path, hub.url, **config)
        started.append(b)
        return b

    yield launch
    for b in started:
        b.stop()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[Any]
) -> Generator[None, Any, None]:
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed:
        tmp = getattr(item, "funcargs", {}).get("tmp_path")
        log = Path(tmp) / "bench-output.txt" if tmp else None
        if log is not None and log.is_file():
            text = log.read_bytes().decode("utf-8", "replace")[-4000:]
            report.sections.append(("bench output (last 4000 chars)", text))
