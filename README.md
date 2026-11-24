<h1><div align="center">
 <img alt="tail" width="300px" height="auto" src="https://github.com/pipecat-ai/tail/raw/refs/heads/main/tail.png">
</div></h1>

[![PyPI](https://img.shields.io/pypi/v/pipecat-ai-tail)](https://pypi.org/project/pipecat-ai-tail) [![Discord](https://img.shields.io/discord/1239284677165056021)](https://discord.gg/pipecat)

# ᓚᘏᗢ Tail: a terminal for voice agents

When you talk to your voice bot, you only hear half of what happened. Did it decide you'd finished, or cut you off mid-sentence? Was that second of silence the LLM, a tool call or the speech synthesis? What did the transcription hear, and what did the LLM actually get?

**Tail** shows you while you talk. It's a terminal dashboard for bots built with [Pipecat](https://github.com/pipecat-ai/pipecat), the open source framework for voice and multimodal conversational AI. It sits next to your bot, in a side pane as narrow as 48 columns or across the whole screen, and attaching it takes one line.

<p align="center"><img src="https://raw.githubusercontent.com/pipecat-ai/tail/refs/heads/main/tail-image.gif" alt="Tail playing its demo session: the voice-to-voice clock counting while the bot calls a flight search tool, then the bot speaking; a typed turn, a transport error and a turn Pipecat ended before the user had finished; then n stepping back through the turns where something went wrong and f following the call again" width="900"/></p>

## Try it

```bash
uv tool install "pipecat-ai[cli]" --with pipecat-ai-tail
pipecat tail --demo
```

The demo plays a scripted call, with tool calls, an interruption, a typed message, a photo, an error and a turn that ended too early, so you can look around before [bringing your own bot](#getting-started).

## What it shows

- **The wait, as it happens.** The voice-to-voice time, from you going silent to the bot speaking, counts up in large digits while the bot thinks, next to where the time is going right now: end of turn, LLM, tools, first sentence, speech synthesis. Once the bot speaks, Pipecat's measured breakdown takes its place.
- **Every turn.** What you said or typed, the tool calls with their arguments and results, and the bot's words, bright once spoken and dim until then, so you see exactly where an interruption stopped it.
- **What went wrong.** Early ends of turn, cut-offs, slow answers, turns the LLM marked incomplete and errors are flagged on their turn, and `n` `p` jump between them.
- **Both voices on a timeline**, with raw VAD, the end of each turn, each response time, backchannels and interruptions.
- **The raw data underneath.** Enter opens a turn: where its time went, the context the LLM saw, and the events Tail received for it.
- **The rest of the pipeline.** Every processor and the frames moving through it, startup timing, service metrics, token usage, workers, jobs and logs.

## A tour

The top of the screen is always there. It shows:

- The bot's state: **LISTENING**, **THINKING** or **SPEAKING**.
- The voice-to-voice clock, and where the current turn's time is going.
- On wide terminals, the session: median and worst response (p95 once there are 20 answers), early ends, interruptions and errors.

Response times are green under your target, amber up to 1.6 times it, and red above. ✓, ▲ and ✕ mark them too, so they never rely on color alone. The target is 1 s; `pipecat tail --target 0.8` changes it in a terminal of its own.

Below the top are five views:

| View | What it shows |
| --- | --- |
| **1 Live** | The voice timeline over the conversation, following the latest turn |
| **2 Latency** | Where each answer's time went, and how soon your turns ended |
| **3 Pipeline** | The processors and their frames, startup timing, workers, jobs and the bus |
| **4 Metrics** | Each service's timings, then tokens, characters and audio used |
| **5 Logs** | Log records with a level filter, search and pinned errors |

**Live** follows the call. The text stays put until it fills the panel, then jumps so the latest line is in the middle. `n` and `p` jump between the turns where something went wrong.

**The turn inspector** opens on Enter. It has three pages:

- **turn**: what was said, where the time went, and the tool calls.
- **context**: what the LLM saw, with what's new marked and long texts clipped.
- **events**: what Tail received for the turn, all but audio levels, flow, logs and the bus.

**Early ends of turn** are flagged `▲ early end`. Pipecat ended your turn at a pause, and you went on before the bot spoke. Tail marks them on the voice timeline, counts them, and explains each one in the inspector. They tell you to tune the VAD's `stop_secs` or your turn analyzer.

A processor error shows as a red line at the top until you dismiss it. When a call ends, a dialog sums it up.

### Keys

| Key | Where | What it does |
| --- | --- | --- |
| `1`–`5` | Anywhere | Switch view |
| `?` | Anywhere | Help, starting with what each mark means |
| `s` | Anywhere | Save a report |
| `w` | Anywhere | Switch worker |
| `t` | Anywhere | Switch theme |
| `c` | Anywhere | Reconnect |
| `x` | Anywhere | Dismiss the error line |
| `e` | Anywhere | Show the latest error in Logs |
| `q` | Anywhere | Quit, asking first once a bot has shown up |
| `↑` `↓` | Live, Latency | Select a turn |
| `⏎` | Live, Latency | Inspect the turn |
| `n` `p` | Live | Next or previous issue, or search match |
| `+` `-` | Live | Zoom the voice timeline |
| `n` `p` | Logs | Next or previous error |
| `l` | Logs | Cycle the log level |
| `/` | Live, Logs | Search |
| `esc` | Live, Logs | End the search |
| `f` | Live, Logs | Follow, or pause following |
| `b` | Pipeline | Cycle the bus filter |
| `tab` | Inspector | Next page |
| `←` `→` | Inspector | Previous or next turn |
| `y` | Inspector | Copy the turn as JSON |

## Getting started

### Requirements

- Python 3.11+
- Pipecat 1.0+ (see [Pipecat versions](#pipecat-versions))

### Install

Add Tail to your bot's project:

```bash
uv add pipecat-ai-tail
```

To watch from another terminal, or another machine, also install the `pipecat tail` command. It ships with Pipecat's `cli` extra:

```bash
uv tool install "pipecat-ai[cli]" --with pipecat-ai-tail
```

Keep both on the same version of Tail.

### Pipecat 1.3 and later

Add a `TailWorker` to the runner, next to your bot's worker:

```python
from pipecat_tail import TailWorker

runner = WorkerRunner()
await runner.add_workers(worker, TailWorker(worker))
await runner.run()
```

The dashboard opens in the bot's own terminal. Quitting it stops the bot. While it's open, the bot's output is kept off the terminal and shown when it closes. Without a terminal (in a container, say), Tail serves it for `pipecat tail` instead.

To watch from another terminal instead, pass `server=True` and run `pipecat tail` there:

```python
await runner.add_workers(worker, TailWorker(worker, server=True))
```

### Pipecat 1.0 to 1.2

Add a `TailObserver` to your pipeline task, and run `pipecat tail` in another terminal:

```python
from pipecat_tail import TailObserver

task = PipelineTask(pipeline, observers=[TailObserver()])
```

You get everything except workers, jobs and the bus, which need Pipecat 1.3.

### Without changing the bot's code

Pipecat runs the setup files listed in `PIPECAT_SETUP_FILES`. Call `setup_tail` from one to add Tail to any bot:

```python
# tail_setup.py
from pipecat_tail import setup_tail

async def setup_pipeline_worker(worker):
    await setup_tail(worker)

async def setup_pipeline_task(task):  # Pipecat 1.0 to 1.2
    await setup_tail(task)

async def setup_worker_runner(runner):  # Pipecat 1.3+ warns when a setup file has none
    pass
```

```bash
PIPECAT_SETUP_FILES=tail_setup.py python bot.py
```

On Pipecat 1.8 and later the dashboard opens in the bot's terminal, as with a `TailWorker`; `setup_tail(worker, server=True)` serves it for `pipecat tail` instead. Before 1.8, run `pipecat tail` in another terminal. The file stays yours: the same hooks can add observers of your own.

## Pipecat versions

Tail works with every Pipecat 1.x release. It looks for what the bot's Pipecat has rather than checking version numbers, and reads the same information from RTVI where a newer observer is missing, so older bots see most of Tail:

| Pipecat | What Tail adds                                                                                                                                                    |
|---------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1.0     | Conversation, audio levels, raw VAD, speaking state, early ends of turn, latency breakdowns, turns, startup timing, metrics, tool calls, errors, frame flow, logs |
| 1.3     | Workers, jobs and the bus (`TailWorker`)                                                                                                                          |
| 1.9     | Richer metrics, tool call and error reports from Pipecat's dedicated observers; latency breakdowns by contribution                                                |
| 1.11    | The LLM's turn-completion markers                                                                                                                                 |
| 1.13    | `user-input`: typed messages and backchannels; the raw LLM and TTS text                                                                                           |
| 1.15    | The bot's delivery tags and the user's tone                                                                                                                       |
| 1.16    | Diarized speakers                                                                                                                                                 |

Tail stops forwarding the RTVI messages Pipecat 1.13 deprecated as soon as their replacements exist, so it keeps working when 2.0 removes them.

## Keeping a session

`s` saves the session in the current directory as `tail-report-<time>.md`, to read or paste into an issue, and `.json`, with every turn (what was said, latency by stage, tool calls, interruptions, early ends) and the session's percentiles, stage breakdown, usage and errors. `pipecat tail --report session.md` writes one when you quit.

For scripts and CI, `--json` runs without the UI and prints the session as JSON lines: a `turn` event as each turn ends, `error` events, and a `session_finished` summary. It exits when the bot goes away:

```bash
pipecat tail --json | jq 'select(.event == "turn") | {turn, latency_secs, grade}'
```

## A bot on another machine

Tail listens on `localhost` only, and the messages it sends include everything said in the call, so don't open the port to the network. Tunnel it instead:

```bash
ssh -L 9292:localhost:9292 bot-host
pipecat tail
```

## Reconnecting

The standalone app reconnects on its own with backoff when the bot restarts. Press `c` to retry immediately. A Tail that connects late, or again, gets the call as it is: the latest messages and everything they build on, like the pipeline's startup and the LLM context. A restarted bot starts a fresh session.

## Metrics

The time spent in each stage (LLM, TTS, STT) and service metrics need metrics enabled on the pipeline:

```python
PipelineWorker(pipeline, params=PipelineParams(enable_metrics=True, enable_usage_metrics=True))
```

Without them Tail still shows the total response time, the conversation, the voice timeline, workers and logs.

## Contributing

```bash
uv sync --group dev
uv run pytest
```

`examples/bot.py` is a voice bot with Tail attached (Deepgram, OpenAI and Cartesia keys in the environment or a `.env` file):

```bash
uv run --group examples examples/bot.py
```

That opens the dashboard in the same terminal with a `TailWorker`. `TAIL_RUNNER=observer` runs it with a `TailObserver` instead; then run `pipecat tail` in another terminal.

Changes are recorded as [towncrier](https://towncrier.readthedocs.io/) fragments in `changelog/`. For a release, the "Generate Changelog for Release" action builds `CHANGELOG.md` from them and opens a pull request.

## 📚 Next steps

- [Pipecat](https://github.com/pipecat-ai/pipecat)
- [Whisker](https://github.com/pipecat-ai/whisker), a browser-based frame debugger for Pipecat
- Join the [Discord](https://discord.gg/pipecat)
