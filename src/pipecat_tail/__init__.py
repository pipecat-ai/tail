#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Tail, a terminal dashboard for Pipecat.

The public API is ``TailObserver``, ``TailServer`` and ``TailRunner``. They
are imported lazily so that the app itself, which does not need Pipecat, can
start quickly.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipecat_tail.observer import TailObserver
    from pipecat_tail.runner import TailRunner
    from pipecat_tail.server import TailServer

__all__ = ["TailObserver", "TailRunner", "TailServer"]


def __getattr__(name: str):
    if name == "TailObserver":
        from pipecat_tail.observer import TailObserver

        return TailObserver
    if name == "TailServer":
        from pipecat_tail.server import TailServer

        return TailServer
    if name == "TailRunner":
        from pipecat_tail.runner import TailRunner

        return TailRunner
    raise AttributeError(f"module 'pipecat_tail' has no attribute '{name}'")
