# Changelog

All notable changes to **Tail** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- towncrier release notes start -->

## [1.0.0] - 2026-11-23

### Added

- Support for Pipecat 1.x, from 1.0 to the latest release.
  (PR [#11](https://github.com/pipecat-ai/tail/pull/11))

- A turn inspector, a summary when a call ends, Markdown and JSON reports (`s`,
  `--report`), `--json` for CI, `--demo` to try Tail without a bot, `--target`
  and `--exit-on-disconnect`.
  (PR [#11](https://github.com/pipecat-ai/tail/pull/11))

- `setup_tail(worker)` adds Tail to a bot from a Pipecat setup file, without
  changing its code.
  (PR [#11](https://github.com/pipecat-ai/tail/pull/11))

- `TailWorker` (Pipecat 1.3+): add it to a `WorkerRunner` beside the bot,
  `await runner.add_workers(worker, TailWorker(worker))`, to also see the
  runner's workers, jobs and bus. It opens the dashboard in the bot's terminal,
  or with `server=True` serves it to `pipecat tail`.
  (PR [#11](https://github.com/pipecat-ai/tail/pull/11))

- Early ends of turn (Pipecat ending your turn at a pause, then you going on)
  are flagged and explained.
  (PR [#11](https://github.com/pipecat-ai/tail/pull/11))

### Changed

- Python 3.11 and Pipecat 1.0 are now the minimums.
  (PR [#11](https://github.com/pipecat-ai/tail/pull/11))

- The dashboard is redesigned around response time, with Live, Latency,
  Pipeline, Metrics and Logs views, dark and light themes, and layouts from a
  48-column side pane to a full screen.
  (PR [#11](https://github.com/pipecat-ai/tail/pull/11))

- `TailObserver` is no longer an `RTVIObserver`, so the bot keeps its own RTVI.
  It reports latency breakdowns, the LLM context, audio levels, frame flow and
  logs.
  (PR [#11](https://github.com/pipecat-ai/tail/pull/11))

### Removed

- `TailRunner`: add a `TailWorker` to a `WorkerRunner` instead.
  (PR [#11](https://github.com/pipecat-ai/tail/pull/11))

## [0.0.5] - 2026-03-30

### Changed

- Upgraded the `websockets` package to support `<16` as the upper bound.

## [0.0.4] - 2026-03-12

### Fixed

- Fixed an issue where RTVI messages names changes in 0.0.105.

## [0.0.3] - 2026-03-12

### Fixed

- Fixed an issue where the RTVI message label module path changed in 0.0.105.

## [0.0.2] - 2025-10-24

### Changed

- Scripts `pipecat-tail` and `pctail` have been removed. Install `pipecat-cli`
  instead and use with `pipecat tail`.

## [0.0.1] - 2025-10-02

Initial public release.
