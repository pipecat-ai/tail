<h1><div align="center">
 <img alt="tail" width="300px" height="auto" src="https://github.com/pipecat-ai/tail/raw/refs/heads/main/tail.png">
</div></h1>

[![PyPI](https://img.shields.io/pypi/v/pipecat-ai-tail)](https://pypi.org/project/pipecat-ai-tail) [![Discord](https://img.shields.io/discord/1239284677165056021)](https://discord.gg/pipecat)

# ᓚᘏᗢ Tail: a terminal for voice agents

When you talk to your voice bot, you only hear half of what happened. Did it decide you'd finished, or cut you off mid-sentence? Was that second of silence the LLM, a tool call or the speech synthesis? What did the transcription hear, and what did the LLM actually get?

**Tail** shows you while you talk. It's a terminal dashboard for bots built with [Pipecat](https://github.com/pipecat-ai/pipecat), the open source framework for voice and multimodal conversational AI. It sits next to your bot, in a side pane as narrow as 48 columns or across the whole screen, and attaching it takes no code at all.

<p align="center"><img src="https://raw.githubusercontent.com/pipecat-ai/tail/refs/heads/main/tail-image.gif" alt="Tail playing its demo session: the voice-to-voice clock counting while the bot calls a flight search tool, then the bot speaking; a typed turn, a transport error and a turn Pipecat ended before the user had finished; then n stepping back through the turns where something went wrong and f following the call again" width="900"/></p>

## Try it

```bash
uv tool install "pipecat-ai[cli]" --with pipecat-ai-tail
pipecat tail --demo
```

The demo plays a scripted call, with tool calls, an interruption, a typed message, a photo, an error and a turn that ended too early, so you can look around before bringing your own bot. Then start your bot with Tail's setup file, and Tail next to it:

```bash
PIPECAT_SETUP_FILES=$(pipecat tail setup-file) python bot.py
pipecat tail    # in another terminal
```

## What it shows

- **The wait, as it happens.** The voice-to-voice time, from you going silent to the bot speaking, counts up in large digits while the bot thinks, next to where the time is going right now: end of turn, LLM, tools, first sentence, speech synthesis. Once the bot speaks, Pipecat's measured breakdown takes its place.
- **Every turn.** What you said or typed, the tool calls with their arguments and results, and the bot's words, bright once spoken and dim until then, so you see exactly where an interruption stopped it.
- **What went wrong.** Early ends of turn, cut-offs, slow answers, turns the LLM marked incomplete and errors are flagged on their turn, and `n` `p` jump between them.
- **Both voices on a timeline**, with raw VAD, the end of each turn, each response time, backchannels and interruptions.
- **The raw data underneath.** Enter opens a turn: where its time went, the exact context the LLM saw, and every event Tail received.
- **The rest of the pipeline.** Every processor and the frames moving through it, startup timing, service metrics, token usage, workers, jobs and logs.

## A tour

The top of the screen is always there: the bot's state (**LISTENING**, **THINKING** or **SPEAKING**), the voice-to-voice clock, the current turn's time so far and, on wide terminals, the session: median and worst response, answers on target, early ends, interruptions and errors. A processor error shows as a red line until `x` dismisses it (`e` finds it in the logs). The status line says whether a call is on (`● call 2:31`), ended, or the bot is gone and Tail is reconnecting.

Response times are green under your target, amber up to 1.6 times it and red above, with ✓, ▲ and ✕ so they never rely on color alone. The target is 1 s; set yours with `pipecat tail --target 0.8`.

Below the top are five views. The key line at the bottom lists the current view's keys:

| View | What it shows |
| --- | --- |
| **1 Live** | The voice timeline over the conversation, following the latest turn |
| **2 Latency** | Where each answer's time went, stage by stage, and how soon your turns ended |
| **3 Pipeline** | The processors and the frames moving through them, startup timing, workers, jobs and the bus |
| **4 Metrics** | Each service's timings (last, recent, median, worst), then tokens, characters and audio used |
| **5 Logs** | Log records with a level filter, search and pinned errors |

**Live** follows the call like a page: the text stays put while it fills the panel, and when it reaches the bottom the panel jumps so the latest line is in the middle, so you read down instead of watching lines crawl up. To look back, `n` jumps to the next turn where something went wrong and `p` to the previous one; `/` searches what you and the bot said, and `n` `p` then step through the matches. While you look back, and once the call is over, the voice timeline keeps only its lanes and the key line shows `f follow` in amber: `f` follows the latest turn again.

**The turn inspector** opens on Enter, three pages that end at the raw data (`tab` moves between them, `←` `→` between turns, `y` copies the turn as JSON):

- **turn**: what was said, where the time went, tool calls pretty-printed, delivery and the turn-completion marker; for an early end, what Pipecat heard and how soon you went on.
- **context**: what the LLM saw for this turn, with what's new since the last turn marked: the system instruction, the tools offered with their parameters, and every message, tool calls and results included, images as thumbnails.
- **events**: every message Tail received for the turn, in order, timed from when you started speaking.

**Early ends of turn** are the bug you can feel but can't see. When you pause mid-sentence, Pipecat may decide you've finished: the LLM starts on what it heard, and when you go on, what you say next reaches it as another message. Tail flags such turns `▲ early end`, marks them on the voice timeline, counts them, and explains them in the turn inspector with how soon you went on. They're the signal for tuning the VAD's `stop_secs` or your turn analyzer, and the Latency view's speech lanes put raw VAD next to each turn's end to help.

### Keys

- Anywhere: `1`–`5` switch views, `?` shows help (starting with what each mark on the screen means), `x` dismisses the error banner, `s` saves a report, `w` switches worker, `t` switches theme, `c` reconnects, `q` quits.
- Turns, in Live and Latency: `↑` `↓` select a turn, `⏎` inspects it.
- Live: `n` `p` jump to the next or previous issue, `/` searches what was said, `esc` ends the search, `f` follows or pauses, `+` `-` zoom the voice timeline.
- Logs: `n` `p` select the next or previous error, `/` searches, `l` cycles the level, `f` follows or pauses. `e`, anywhere, shows the latest error here.
- Pipeline: `b` cycles the bus filter.

## Getting started

### Requirements

- Python 3.11+
- Pipecat 1.0+ (see [Pipecat versions](#pipecat-versions))

### Install

The standalone app is a `pipecat` CLI command, which ships with Pipecat's `cli` extra:

```bash
uv tool install "pipecat-ai[cli]" --with pipecat-ai-tail
```

To use Tail from your bot's code (options B and C), add it to the bot's environment:

```bash
uv pip install pipecat-ai-tail
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

Every pipeline worker the bot runs gets a Tail observer, and a Tail server worker joins the runner so workers, jobs and bus traffic show up too. With Pipecat 1.0 to 1.2, which run pipelines as a `PipelineTask`, each task gets a Tail observer. Set `TAIL_HOST` and `TAIL_PORT` to change where the server listens (default `localhost:9292`).

### Option B: observer in code

Add `TailObserver` to your pipeline worker (a `PipelineTask` before Pipecat 1.3). It serves its own websocket:

```python
from pipecat_tail import TailObserver

worker = PipelineWorker(pipeline, observers=[TailObserver()])
```

To also see workers, jobs and the bus (Pipecat 1.3+), add a `TailServer` to the runner and let it create the observer:

```python
from pipecat_tail import TailServer

tail = TailServer()
runner = WorkerRunner()
await runner.add_workers(worker, tail)
worker.add_observer(tail.create_observer(worker))
await runner.run()
```

### Option C: runner with the app built in

`TailRunner` is a `WorkerRunner` (Pipecat 1.3+) that opens the dashboard in the same terminal, no separate command needed:

```python
from pipecat_tail import TailRunner

runner = TailRunner()
await runner.add_workers(worker)
await runner.run()
```

While the dashboard is open the bot's output is kept off the terminal, and shown there once it closes (output to a file is untouched). Quitting it with `q` stops the bot, and the bot's own log shows it shutting down. When the bot finishes first, the dashboard stays open until you quit.

## Pipecat versions

Tail works with every Pipecat 1.x release. It looks for what the bot's Pipecat has rather than checking version numbers, and reads the same information from RTVI where a newer observer is missing, so older bots see most of Tail:

| Pipecat | What Tail adds                                                                                                                                                    |
|---------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1.0     | Conversation, audio levels, raw VAD, speaking state, early ends of turn, latency breakdowns, turns, startup timing, metrics, tool calls, errors, frame flow, logs |
| 1.3     | Workers, jobs and the bus (`TailServer`, `TailRunner`)                                                                                                            |
| 1.9     | Richer metrics, tool call and error reports from Pipecat's dedicated observers; latency breakdowns by contribution                                                |
| 1.11    | The LLM's turn-completion markers                                                                                                                                 |
| 1.13    | `user-input`: typed messages, backchannels, interim transcriptions; the raw STT, LLM and TTS text                                                                 |
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

The standalone app reconnects on its own with backoff when the bot restarts. Press `c` to retry immediately.

## Metrics

The time spent in each stage (LLM, TTS, STT) and service metrics need metrics enabled on the pipeline. The setup file (option A) turns them on when the bot left them off, and says so in the log; set `TAIL_ENABLE_METRICS=0` to keep them off. In code:

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

That opens the dashboard in the same terminal with `TailRunner` (option C). `TAIL_RUNNER=observer` runs it with a `TailObserver` instead (option B), and `TAIL_RUNNER=0` with neither, for the setup file (option A); then run `pipecat tail` in another terminal.

Changes are recorded as [towncrier](https://towncrier.readthedocs.io/) fragments in `changelog/`.

## 📚 Next steps

- [Pipecat](https://github.com/pipecat-ai/pipecat)
- [Whisker](https://github.com/pipecat-ai/whisker), a browser-based frame debugger for Pipecat
- Join the [Discord](https://discord.gg/pipecat)
