# loop-latency tasks

## 1.

- [ ] 1.1 Skip the predict-time whole-page `fresh()` when the observation is younger than a small threshold; stamp observe time and keep StalePage-on-navigation behaviour (jev_ultrafast/agent.py, tests/test_agent.py). Verify: `uv run ruff check . && uv run pytest`
- [ ] 1.2 Run the dual-engine loop with `screenshots=False`; deadlock diagnosis and final audit keep their own `observe(screenshot=True)` (examples/dual_engine_agent.py). Verify: `uv run ruff check . && uv run pytest`
- [ ] 1.3 Cut the page-text slice in `field_context()` from 6000 to 1500 characters and add a test for the cap (jev_ultrafast/model.py, tests/test_agent.py). Verify: `uv run ruff check . && uv run pytest`

## 2.

- [ ] 2.1 Record a reason code for every discarded decision (which freshness check, or an evaluation exception) in agent state and the history/trace (jev_ultrafast/agent.py, tests/test_agent.py). Verify: `uv run ruff check . && uv run pytest`
- [ ] 2.2 Warm the shared HTTP/2 model client in a background thread during `Agent.__init__`, with no Authorization header, no billable request, errors swallowed; tests mock the network (jev_ultrafast/model.py, jev_ultrafast/agent.py, tests/test_agent.py). Verify: `uv run ruff check . && uv run pytest`
