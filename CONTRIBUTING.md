# Contributing

```bash
git clone https://github.com/pritampanda15/autoharness && cd autoharness
pip install -e '.[dev]'
```

```bash
pytest -q
```

```bash
autoharness demo
```

The test suite makes no API calls: the agent, judge and fixer are scripted. Keep it that way. A new feature should come with an offline test.

Guidelines:
- Graders must be deterministic, or must expose their uncertainty (as the judge does) so it can reach the review queue.
- Anything that can edit files has to go through `loop.autofix`'s guards. Never write to the user's files from a probe.
- New check types go in `graders/deterministic.py` or `graders/trace_checks.py`, are registered in that file's `CHECKS`, and are documented in `docs/CASES.md`.
- Open an issue before a large change.
