#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Tail, a terminal dashboard for Pipecat.

The public API is ``TailObserver`` and ``TailWorker``. They
are imported lazily so that the app itself, which does not need Pipecat, can
start quickly.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipecat_tail.observer import TailObserver
    from pipecat_tail.worker import TailWorker

__all__ = ["TailObserver", "TailWorker"]


def __getattr__(name: str):
    if name == "TailObserver":
        from pipecat_tail.observer import TailObserver

        return TailObserver
    if name == "TailWorker":
        from pipecat_tail.worker import TailWorker

        return TailWorker
    raise AttributeError(f"module 'pipecat_tail' has no attribute '{name}'")
