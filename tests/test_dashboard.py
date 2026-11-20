#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import asyncio
import os
import socket
import sys

import pytest


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
@pytest.mark.asyncio
async def test_the_app_is_pipecat_tail_in_a_process_of_its_own(monkeypatch):
    """Not a fork of the bot: it holds none of the bot's sockets, its port
    among them, and it quits once its connection ends."""
    from pipecat_tail.dashboard import _Dashboard
    from pipecat_tail.sink import TailWebSocketServer

    monkeypatch.setenv("TEXTUAL_DRIVER", "textual.drivers.headless_driver:HeadlessDriver")
    bot_port = socket.socket()
    bot_port.bind(("127.0.0.1", 0))
    bot_port.listen()
    server = TailWebSocketServer(host="127.0.0.1", port=0)
    await server.start()
    process = _Dashboard().start_app(server.url)
    try:
        held = [
            os.readlink(f"/proc/{process.pid}/fd/{fd}")
            for fd in os.listdir(f"/proc/{process.pid}/fd")
        ]
        assert not [link for link in held if link.startswith("socket:")]
        await server.emit({"label": "tail", "type": "tail-ready", "data": {}})
        for _ in range(300):
            if server._client is not None:
                break
            await asyncio.sleep(0.1)
        assert server._client is not None, "pipecat tail did not connect"
        await server.stop()
        assert await asyncio.to_thread(process.wait, 30) == 0
    finally:
        if process.poll() is None:
            process.kill()
        await server.stop()
        bot_port.close()


def test_terminal_output_is_hidden_while_the_app_draws(monkeypatch, capfd):
    import os

    from pipecat_tail.dashboard import _TerminalOutput

    # Pretend stdout and stderr are the terminal (pytest captures them).
    monkeypatch.setattr(os, "isatty", lambda fd: True)
    output = _TerminalOutput()
    output.hide()
    os.write(1, b"while it draws\n")
    os.write(2, b"worker finishing\n")
    # Nothing reaches the terminal while the app is on it.
    assert capfd.readouterr() == ("", "")
    output.restore()
    output.restore()
    os.write(2, b"after\n")
    # Then it shows what was written meanwhile, in order, as if it had been there.
    out, err = capfd.readouterr()
    assert out + err == "while it draws\nworker finishing\nafter\n"


def test_terminal_output_to_a_file_is_left_alone(monkeypatch, capfd):
    import os

    from pipecat_tail.dashboard import _TerminalOutput

    monkeypatch.setattr(os, "isatty", lambda fd: False)
    output = _TerminalOutput()
    output.hide()
    os.write(2, b"kept\n")
    output.restore()
    assert "kept" in capfd.readouterr().err
