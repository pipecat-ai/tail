#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Tail, a terminal dashboard for Pipecat.

The public API is ``TailObserver``, ``TailWorker`` and ``setup_tail``. They
are imported lazily so that the app itself, which does not need Pipecat, can
start quickly.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipecat_tail.observer import TailObserver
    from pipecat_tail.setup import setup_tail
    from pipecat_tail.worker import TailWorker

__all__ = ["TailObserver", "TailWorker", "setup_tail"]


def __getattr__(name: str):
    if name == "TailObserver":
        from pipecat_tail.observer import TailObserver

        return TailObserver
    if name == "TailWorker":
        from pipecat_tail.worker import TailWorker

        return TailWorker
    if name == "setup_tail":
        from pipecat_tail.setup import setup_tail

        return setup_tail
    raise AttributeError(f"module 'pipecat_tail' has no attribute '{name}'")
