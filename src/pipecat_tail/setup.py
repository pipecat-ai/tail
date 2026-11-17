#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Setup file that attaches Tail to any Pipecat bot without code changes.

Pipecat loads the files listed in ``PIPECAT_SETUP_FILES`` and calls the hooks
they define. This module implements the hooks of every Pipecat 1.x release, so
pointing the variable at it is the whole integration::

    PIPECAT_SETUP_FILES=$(pipecat tail setup-file) python bot.py
    pipecat tail

- Pipecat 1.3 and later call ``setup_worker_runner`` and
  ``setup_pipeline_worker``. Each runner gets its own ``TailServer``, so
  workers, jobs and bus traffic show up too.
- Pipecat 1.0 to 1.2 call ``setup_pipeline_task``, which adds a standalone
  ``TailObserver`` to the task.

The server binds to ``TAIL_HOST`` and ``TAIL_PORT`` when set, otherwise
``localhost:9292``.

Tail shows where each answer's time goes from Pipecat's metrics, so the hooks
turn on ``enable_metrics`` and ``enable_usage_metrics`` when the bot left them
off (and say so in the log). Set ``TAIL_ENABLE_METRICS=0`` to leave them as
they are.
"""

import os
import weakref

from loguru import logger

from pipecat_tail.sink import DEFAULT_HOST, DEFAULT_PORT

_servers: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _address() -> tuple[str, int]:
    host = os.environ.get("TAIL_HOST", DEFAULT_HOST)
    port = int(os.environ.get("TAIL_PORT", str(DEFAULT_PORT)))
    return host, port


def _enable_metrics(worker) -> None:
    if os.environ.get("TAIL_ENABLE_METRICS", "1").lower() in ("0", "false", "no"):
        return
    params = getattr(worker, "_params", None)
    if params is None:
        return
    changed = []
    for name in ("enable_metrics", "enable_usage_metrics"):
        if getattr(params, name, None) is False:
            try:
                setattr(params, name, True)
                changed.append(name)
            except Exception:
                pass
    if changed:
        logger.info(
            f"ᓚᘏᗢ Tail: turned on {' and '.join(changed)} to show where the time goes "
            "(TAIL_ENABLE_METRICS=0 to leave them off)"
        )


def _standalone_observer(worker):
    from pipecat_tail.observer import TailObserver

    host, port = _address()
    return TailObserver(host=host, port=port, worker=worker)


async def setup_worker_runner(runner):
    """Add a Tail server worker to the runner (Pipecat 1.3+).

    Args:
        runner: The ``WorkerRunner`` being set up.
    """
    from pipecat_tail.server import TailServer

    host, port = _address()
    server = TailServer(host=host, port=port)
    _servers[runner] = server
    await runner.add_workers(server)


async def setup_pipeline_worker(worker):
    """Attach a Tail observer to the pipeline worker (Pipecat 1.3+).

    Args:
        worker: The ``PipelineWorker`` being set up.
    """
    _enable_metrics(worker)
    server = None
    try:
        server = _servers.get(worker.worker_runner)
    except (RuntimeError, AttributeError, TypeError):
        pass
    if server is None:
        # No runner hook ran (for example the worker runs on a plain runner
        # without setup files); fall back to a standalone observer.
        worker.add_observer(_standalone_observer(worker))
        return
    worker.add_observer(server.create_observer(worker))


async def setup_pipeline_task(task):
    """Attach a standalone Tail observer to the pipeline task (Pipecat 1.0 to 1.2).

    Args:
        task: The ``PipelineTask`` being set up.
    """
    _enable_metrics(task)
    task.add_observer(_standalone_observer(task))


if __name__ == "__main__":
    print(os.path.abspath(__file__))
