#
# Copyright (c) 2025-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""``setup_tail``: Tail for a pipeline, from a Pipecat setup file or anywhere.

Pipecat runs the hooks of the files listed in ``PIPECAT_SETUP_FILES``, so a
bot gets Tail without changing its code::

    # tail_setup.py
    from pipecat_tail import setup_tail

    async def setup_pipeline_worker(worker):
        await setup_tail(worker)

    async def setup_pipeline_task(task):  # Pipecat 1.0 to 1.2
        await setup_tail(task)

The setup file stays yours: the same hooks can add observers or workers of
your own.
"""

import weakref
from typing import Any, Optional

from pipecat_tail.observer import TailObserver

# Each runner's Tail worker, made with the runner's first pipeline.
_tails: "weakref.WeakKeyDictionary[Any, Any]" = weakref.WeakKeyDictionary()
# The pipelines Tail already reports on.
_set_up: "weakref.WeakSet[Any]" = weakref.WeakSet()


async def setup_tail(target: Any, *, server: bool = False, **kwargs: Any) -> None:
    """Report a pipeline worker, or a pipeline task before Pipecat 1.3, to Tail.

    A pipeline worker on a runner gets its runner's ``TailWorker`` (Pipecat
    1.8+), made with the runner's first pipeline: the dashboard opens in this
    terminal (served for ``pipecat tail`` without one, or with ``server``).
    Anything else gets a ``TailObserver``, served for ``pipecat tail``.
    Calling it again for the same pipeline does nothing.

    Args:
        target: The ``PipelineWorker``, or the ``PipelineTask``.
        server: Serve the dashboard for ``pipecat tail`` instead of opening
            it in this terminal.
        **kwargs: Passed on to the ``TailWorker`` or ``TailObserver``, such
            as ``port``.
    """
    if target in _set_up:
        return
    _set_up.add(target)
    runner = _runner_of(target)
    if runner is None:
        target.add_observer(TailObserver(**kwargs))
        return
    tail = _tails.get(runner)
    if tail is not None:
        target.add_observer(tail.create_observer(target))
        return
    from pipecat_tail.worker import TailWorker

    tail = _tails[runner] = TailWorker(target, server=server, **kwargs)
    # Pipecat 1.8 to 1.15 start a worker added while the runner is starting
    # its own only once it is running: then the worker is added when the
    # runner is ready. There's no public way to tell before 1.16.
    if getattr(runner, "_running", True):
        await runner.add_workers(tail)
    else:

        async def add_when_ready(runner):
            await runner.add_workers(tail)

        runner.add_event_handler("on_ready", add_when_ready)


def _runner_of(target: Any) -> Optional[Any]:
    """The runner a pipeline worker is on, when Pipecat says (1.8+)."""
    try:
        return target.worker_runner
    except (AttributeError, RuntimeError):
        return None
