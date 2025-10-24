<h1><div align="center">
 <img alt="tail" width="300px" height="auto" src="https://github.com/pipecat-ai/tail/raw/refs/heads/main/tail.png">
</div></h1>

[![PyPI](https://img.shields.io/pypi/v/pipecat-ai-tail)](https://pypi.org/project/pipecat-ai-tail) [![Discord](https://img.shields.io/discord/1239284677165056021)](https://discord.gg/pipecat)

# ᓚᘏᗢ Tail: A terminal dashboard for Pipecat

**Tail** is a terminal dashboard for the [Pipecat](https://github.com/pipecat-ai/pipecat) voice and multimodal conversational AI framework.

It lets you monitor your Pipecat sessions in real time with logs, conversations, metrics, and audio levels all in one place.

With Tail you can:

- 📜 Follow system logs in real time
- 💬 Track conversations as they happen
- 🔊 Monitor user and agent audio levels
- 📈 Keep an eye on service metrics and usage
- 🖥️ Run locally as a pipeline runner or connect to a remote session

<p align="center"><img src="https://raw.githubusercontent.com/pipecat-ai/tail/refs/heads/main/tail-image.gif" alt="Tail" width="500"/></p>

## 🧭 Getting started

### Requirements

- Python 3.10+

### Install Tail for Python

```bash
uv pip install pipecat-ai-tail
```

and also install Pipecat CLI so you can run Tail as a standalone application:

```bash
uv tool install pipecat-ai-cli
```

### ⚡ Option A: Pipeline runner

Use `TailRunner` as a drop-in replacement for `PipelineRunner`. For example:

```python
runner = PipelineRunner()

await runner.run(task)
```

becomes

```python
from pipecat_tail.runner import TailRunner

runner = TailRunner()

await runner.run(task)
```

### 🏠 Option B: Standalone app

You can also start Tail as a standalone application. This lets you connect to a running session, whether local or remote. All you need to do is add the `TailObserver` to your pipeline task:

```python
from pipecat_tail.observer import TailObserver

task = PipelineTask(..., observers=[TailObserver()])
```

Then start the app:

```sh
pipecat tail [--url URL]
```

By default, it will connect to `ws://localhost:9292`.

## 📚 Next steps

- See the [Pipecat documentation](https://docs.pipecat.ai) for more about building bots
