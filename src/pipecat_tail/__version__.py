#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Get installed version of the `pipecat-ai-tail` package."""

from importlib.metadata import version as get_version

version = get_version("pipecat-ai-tail")
