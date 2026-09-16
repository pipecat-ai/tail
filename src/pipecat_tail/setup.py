#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Setup file that attaches Tail to any Pipecat bot without code changes.

Pipecat loads the files listed in ``PIPECAT_SETUP_FILES`` and calls their
``setup_worker_runner`` and ``setup_pipeline_worker`` hooks. This module
implements both, so pointing the variable at it is the whole integration::

    PIPECAT_SETUP_FILES=$(python -m pipecat_tail.setup) python bot.py
    pipecat tail

Each runner gets its own ``TailServer``. The server binds to ``TAIL_HOST``
and ``TAIL_PORT`` when set, otherwise ``localhost:9292``.
"""

import os
import weakref

from pipecat_tail.server import TailServer
from pipecat_tail.sink import DEFAULT_HOST, DEFAULT_PORT

_servers: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _create_server() -> TailServer:
    host = os.environ.get("TAIL_HOST", DEFAULT_HOST)
    port = int(os.environ.get("TAIL_PORT", str(DEFAULT_PORT)))
    return TailServer(host=host, port=port)


async def setup_worker_runner(runner):
    """Add a Tail server worker to the runner.

    Args:
        runner: The ``WorkerRunner`` being set up.
    """
    server = _create_server()
    _servers[runner] = server
    await runner.add_workers(server)


async def setup_pipeline_worker(worker):
    """Attach a Tail observer to the pipeline worker.

    Args:
        worker: The ``PipelineWorker`` being set up.
    """
    server = None
    try:
        server = _servers.get(worker.worker_runner)
    except RuntimeError:
        pass
    if server is None:
        # No runner hook ran (for example the worker runs on a plain runner
        # without setup files); fall back to a standalone observer.
        from pipecat_tail.observer import TailObserver

        worker.add_observer(
            TailObserver(
                host=os.environ.get("TAIL_HOST", DEFAULT_HOST),
                port=int(os.environ.get("TAIL_PORT", str(DEFAULT_PORT))),
                worker=worker,
            )
        )
        return
    worker.add_observer(server.create_observer(worker))


if __name__ == "__main__":
    print(os.path.abspath(__file__))
