#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import threading
import time
from multiprocessing import Queue

import pytest

from pipecat_tail import compat

pytestmark = pytest.mark.skipif(not compat.HAS_WORKERS, reason="TailRunner needs Pipecat 1.3+")


def start_app(queue):
    """Run the app the way the app process does, headless, in a thread."""
    from pipecat_tail.runner import TailAppProcess

    process = TailAppProcess(queue, headless=True)
    finished = threading.Event()

    def run():
        process.run_app()
        finished.set()

    threading.Thread(target=run, daemon=True).start()
    end = time.monotonic() + 10
    while process._reader_task is None and time.monotonic() < end:
        time.sleep(0.05)
    assert process._reader_task is not None, "the app did not start"
    return process, finished


def test_app_exits_while_its_queue_is_idle():
    # Quitting after the pipeline has stopped sending used to hang: the
    # queue read kept the app's event loop from closing.
    queue = Queue()
    process, finished = start_app(queue)
    queue.put({"type": "tail-ready"})
    time.sleep(0.2)
    process._app.call_from_thread(process._app.exit)
    assert finished.wait(10), "the app did not exit"


def test_quit_message_closes_the_app():
    from pipecat_tail.runner import QUIT

    queue = Queue()
    _, finished = start_app(queue)
    queue.put(QUIT)
    assert finished.wait(10), "the app did not exit"


def test_end_of_messages_keeps_the_app_open():
    queue = Queue()
    process, finished = start_app(queue)
    queue.put(None)
    assert not finished.wait(0.5)
    process._app.call_from_thread(process._app.exit)
    assert finished.wait(10)


def test_terminal_output_is_hidden_while_the_app_draws(monkeypatch, capfd):
    import os

    from pipecat_tail.runner import _TerminalOutput

    # Pretend stdout and stderr are the terminal (pytest captures them).
    monkeypatch.setattr(os, "isatty", lambda fd: True)
    output = _TerminalOutput()
    output.hide()
    os.write(1, b"hidden\n")
    os.write(2, b"hidden\n")
    output.restore()
    output.restore()
    os.write(2, b"shown\n")
    out, err = capfd.readouterr()
    assert "hidden" not in out + err
    assert "shown" in err


def test_terminal_output_to_a_file_is_left_alone(monkeypatch, capfd):
    import os

    from pipecat_tail.runner import _TerminalOutput

    monkeypatch.setattr(os, "isatty", lambda fd: False)
    output = _TerminalOutput()
    output.hide()
    os.write(2, b"kept\n")
    output.restore()
    assert "kept" in capfd.readouterr().err
