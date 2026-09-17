<h1><div align="center">
 <img alt="tail" width="300px" height="auto" src="https://github.com/pipecat-ai/tail/raw/refs/heads/main/tail.png">
</div></h1>

[![PyPI](https://img.shields.io/pypi/v/pipecat-ai-tail)](https://pypi.org/project/pipecat-ai-tail) [![Discord](https://img.shields.io/discord/1239284677165056021)](https://discord.gg/pipecat)

# ᓚᘏᗢ Tail: A terminal dashboard for Pipecat

**Tail** is a terminal dashboard for the [Pipecat](https://github.com/pipecat-ai/pipecat) voice and multimodal conversational AI framework.

It shows a running bot as it happens: the conversation with the bot's words highlighted as they are spoken, per-turn latency broken down by service, workers and jobs, service metrics, and logs, all in one terminal window.

<p align="center"><img src="https://raw.githubusercontent.com/pipecat-ai/tail/refs/heads/main/tail-image.gif" alt="Tail" width="500"/></p>

## What you see

A strip at the top always shows the connection, the selected worker, the current turn, the last user-to-bot latency, token and character usage, and the user and bot audio meters. Errors appear in a banner until dismissed. Below it, five tabs:

| Tab | What it shows |
| --- | --- |
| **1 Conversation** | One block per turn. The LLM output is dim until the TTS has spoken it, interim transcriptions resolve in place, tool calls appear inline with arguments, result and duration, and interruptions mark where the bot was cut off. |
| **2 Latency** | A stacked bar per turn from the user going silent to the bot speaking, split into endpointing, transcription, LLM inference, function handlers and speech synthesis, with p50 and p95. A timeline separates raw VAD from the turn ruling so `stop_secs` and smart-turn tuning is visible. |
| **3 Workers** | The runner's workers as a tree with each pipeline's shape, a jobs table, and the bus traffic as it happens. |
| **4 Metrics** | A chart of TTFB and other service latencies with selectable series, a summary with last, average and p95, usage totals (tokens, characters, STT audio seconds), and the startup timing report per processor. |
| **5 Logs** | Structured log records with a level filter, regex search, pause and pinned errors. |

Keys: `1`–`5` switch tabs, `f` pauses or resumes following, `/` searches the logs, `l` cycles the log level, `b` cycles the bus filter, `e` jumps to the next error, `x` dismisses the banner, `w` cycles the selected worker, `s` starts or stops recording the session, `c` reconnects, `q` quits.

## Getting started

### Requirements

- Python 3.11+
- Pipecat 1.3+

### Install

```bash
uv pip install pipecat-ai-tail
```

The standalone app is a `pipecat` CLI command. The CLI ships with Pipecat's `cli` extra:

```bash
uv tool install "pipecat-ai[cli]" --with pipecat-ai-tail
```

### Option A: attach without touching the bot

Pipecat loads setup files listed in `PIPECAT_SETUP_FILES`. Tail ships one, so this is the whole integration:

```bash
PIPECAT_SETUP_FILES=$(pipecat tail setup-file) python bot.py
```

then, in another terminal:

```bash
pipecat tail
```

Every pipeline worker the bot runs gets a Tail observer, and a Tail server worker joins the runner so workers, jobs and bus traffic show up too. Set `TAIL_HOST` and `TAIL_PORT` to change where the server listens (default `localhost:9292`).

### Option B: observer in code

Add `TailObserver` to your pipeline worker. It serves its own websocket:

```python
from pipecat_tail import TailObserver

worker = PipelineWorker(pipeline, observers=[TailObserver()])
```

Pass `host="0.0.0.0"` to reach it from another machine, then:

```bash
pipecat tail --url ws://bot-host:9292
```

To also see workers, jobs and the bus, add a `TailServer` to the runner and let it create the observer:

```python
from pipecat_tail import TailServer

tail = TailServer()
runner = WorkerRunner()
await runner.add_workers(worker, tail)
worker.add_observer(tail.create_observer(worker))
await runner.run()
```

### Option C: runner with the app built in

`TailRunner` is a `WorkerRunner` that opens the dashboard in the same terminal, no separate command needed:

```python
from pipecat_tail import TailRunner

runner = TailRunner()
await runner.add_workers(worker)
await runner.run()
```

## Recording and replaying sessions

Record what the app receives with `--save`, or press `s` while it runs:

```bash
pipecat tail --save session.jsonl
```

Replay it later, with the original timing, without a bot:

```bash
pipecat tail --replay session.jsonl
pipecat tail --replay session.jsonl --speed 4
```

A session file is JSON Lines with one wire message per line, so it is also easy to inspect with `jq`.

## Reconnecting

The standalone app reconnects on its own with backoff when the bot restarts. Press `c` to retry immediately.

## Metrics

Per-turn latency breakdowns and service metrics need metrics enabled on the pipeline:

```python
PipelineWorker(pipeline, params=PipelineParams(enable_metrics=True, enable_usage_metrics=True))
```

Without them Tail still shows the total user-to-bot latency, the conversation, workers and logs.

## Contributing

```bash
uv sync --group dev
uv run pytest
```

Changes are recorded as [towncrier](https://towncrier.readthedocs.io/) fragments in `changelog/`.

## 📚 Next steps

- [Pipecat](https://github.com/pipecat-ai/pipecat)
- [Whisker](https://github.com/pipecat-ai/whisker), a browser-based frame debugger for Pipecat
- Join the [Discord](https://discord.gg/pipecat)
