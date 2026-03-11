#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""RTVI message models specific to the Tail application.

Defines additional RTVI messages exchanged between the Tail observer and Tail
client.
"""

import platform
from importlib.metadata import version
from typing import Any, Literal, Mapping

try:
    # pipecat >= 0.0.105
    from pipecat.processors.frameworks.rtvi.processor import MESSAGE_LABEL as RTVI_MESSAGE_LABEL
except ImportError:
    from pipecat.processors.frameworks.rtvi import RTVI_MESSAGE_LABEL

from pipecat.processors.frameworks.rtvi import RTVIMessageLiteral
from pydantic import BaseModel

__PIPECAT_VERSION__ = version("pipecat-ai")
__TAIL_VERSION__ = version("pipecat-ai-tail")
__PYTHON_VERSION__ = platform.python_version()


class RTVITailReadyMessage(BaseModel):
    """Message indicating the beginning of a Tail connection."""

    label: RTVIMessageLiteral = RTVI_MESSAGE_LABEL
    type: Literal["tail-ready"] = "tail-ready"
    data: Mapping[str, Any] = {
        "pipecat": __PIPECAT_VERSION__,
        "tail": __TAIL_VERSION__,
        "python": __PYTHON_VERSION__,
    }


class RTVITailPipelineFinishedMessage(BaseModel):
    """Message indicating the pipeline has finished."""

    label: RTVIMessageLiteral = RTVI_MESSAGE_LABEL
    type: Literal["tail-pipeline-finished"] = "tail-pipeline-finished"
