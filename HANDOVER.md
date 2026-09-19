# Handover — what still needs a real browser

**Repo:** `roylam-beep/jev-ultrafast` · **main:** `0634e1d` · **state:** 195 tests + ruff green, 6 PRs merged, 0 open.

Everything below needs either a real Chrome, paid API calls, or your OpenRouter account — none of which the cloud container had. Nothing here is a known bug; it is the list of claims that were made without the verification that would normally back them.

Priority order is top to bottom. **§1 and §2 are the ones that could hide a real regression.**

---

## 0. Get the tree

```bash
git clone https://github.com/roylam-beep/jev-ultrafast.git   # or: git pull
cd jev-ultrafast
uv sync
cp .env.example .env          # paste one OpenRouter key into the three *_API_KEY lines
```

Baseline that should already pass (it passed in CI-less container too):

```bash
uv run ruff check .
uv run pytest                              # expect 195 passed
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/snapshot.js
uv build
```

If any of these fail on your machine, stop and report — they were green at `0634e1d`.

---

## 1. `check_guards.py` — never run, on any of the six PRs

This is the only test that drives a real Chrome through Browser Harness. It exercises the freshness guards, the overlay/occlusion check, native dropdowns, and async combobox suggestions. It was skipped on **every** PR because the container could not start the daemon (`RuntimeError: daemon default didn't come up`).

```bash
uv run browser-harness --doctor     # allow remote debugging in Chrome when prompted
uv run python scripts/check_guards.py
```

**Expect:** a list of passed guard names ending in `PASS: N browser guard checks; no model calls`.

**If it fails**, the line it fails on tells you which layer:

| Failing assertion | Most likely cause | Introduced by |
| --- | --- | --- |
| `moving target clicked at its current location` | geometry resolution | pre-existing |
| `overlay blocked before input` | hit-testing | pre-existing |
| `real text input waits for asynchronous combobox suggestions` | the post-input settle in `Browser.observe` | pre-existing |
| anything mentioning `select` / dropdown | `MUTATING_EVALUATIONS` in `browser.py` | PR #6 |

Note `check_guards.py` does **not** cover scroll — that is §2.

---

## 2. The scroll change — the riskiest unverified claim in the whole set

PR #6 replaced the executor's `Input.dispatchMouseEvent type=mouseWheel` with a synthetic `WheelEvent` + explicit `window.scrollBy`, on the strength of this measurement in **headless Chromium driven by Playwright** — not Browser Harness, not your real headful Chrome:

| | CDP `mouseWheel` | synthetic `WheelEvent` |
|---|---:|---:|
| first wheel of a fresh session | 1/6 | 6/6 |
| steady state, same session | 9/10 | 10/10 |
| tab in the foreground | 10/10 | — |

The claim is that CDP wheel input needs a foreground focused target, and the agent owns a background one. **If that does not reproduce on your setup, the change is unnecessary complexity** and worth reverting to the one-line CDP call.

### Check it

Drive any long page and make the model scroll:

```bash
uv run --env-file .env python examples/run.py \
  --url 'https://en.wikipedia.org/wiki/Special:Random' \
  --goal 'Scroll down until the References section is visible, then stop.'
```

**Expect:** the page actually moves, and `scroll` steps report `page_changed: true`.

**Watch for:** three consecutive scroll steps with `page_changed: false` → the scroll is doing nothing and the run will hit the no-progress stop and go `blocked`.

### If you want the before/after directly

`jev_ultrafast/pointer.py:scroll_expression()` is a pure function — print it and paste it into Chrome DevTools on a background tab, then compare against a raw CDP wheel. Both are one call; the synthetic one returns `true` only if the page actually moved.

### Known limitation, by design

An untrusted `WheelEvent` performs no default scroll, so the scroll is explicit and moves **the document**, not a nested scroller. Nested scrolling was already outside this MVP. A page that calls `preventDefault()` on the wheel stays put, which is what a real wheel would do.

---

## 3. Re-record the measurements

Every number in `docs/` was recorded when `WAIT` was `time.sleep(0.1)`. Both the 7.073 s recording and the three matched-comparison runs contain **exactly one `WAIT` action**, so each carries 100 ms of fixed sleep that no longer exists.

`README.md` and `docs/performance.md` currently carry an explicit note saying so. Re-record and you can delete the note.

```bash
# live measured run, original CDP timestamps retained
uv run --env-file .env python scripts/record_flights.py artifacts/flights/rerecorded

# render that verified run at 1x
uv run python scripts/render_demo.py artifacts/flights/rerecorded

# single measured run without the screencast, for the comparison table
uv run --env-file .env python scripts/measure_flights.py
```

Then update:

- `docs/flights-measurement.json`, `docs/full-speed-measurement.json`
- the 7.073 s / 9.450→7.092 s / 1,092→101 numbers in `README.md` and `docs/performance.md`
- delete the "recorded when WAIT was a fixed sleep" paragraph in both files

**Costs paid API calls.** Also worth noting: the WAIT change can move a single action either way, but it removes the follow-up decision a still-loading page used to cost, so total time should not get worse.

### Tunables if the new WAIT feels wrong

```
jev_ultrafast/browser.py:24   WAIT_BUDGET   = 2.0    # ceiling per WAIT
jev_ultrafast/browser.py:25   WAIT_IDLE_MS  = 250    # quiet window that ends the wait
jev_ultrafast/waits.py:45     IDLE_MS       = 500    # default for the standalone helper
jev_ultrafast/waits.py:46     POLL          = 0.05
```

---

## 4. Confirm the OpenRouter model slug and the probability shape

PR #5 changed the shipped policy default. I could not verify either of these — this environment's egress proxy blocks `openrouter.ai` (`CONNECT tunnel failed, 403`).

**Current shipped defaults:**

| Role | Endpoint | Model |
| --- | --- | --- |
| Policy (operation + target) | `https://openrouter.ai/api/v1` | `typesafe/jev-1.13` |
| Text helper (`TYPE_TEXT`) | same | `zhipu/glm-5.3-flash` |
| Visual supervisor | same | `zhipu/glm-5.3-flash` |

### 4a. Does the slug exist?

```bash
uv run --env-file .env python scripts/probe_provider.py
```

If `typesafe/jev-1.13` is wrong, fix it in **three** places (they must agree — a mismatch was a real bug in PR #1):

```
jev_ultrafast/model.py   DEFAULT_TYPESAFE_MODEL
.env.example             TYPESAFE_MODEL
README.md                the provider table row
```

### 4b. Are the probabilities real or synthetic?

Open the inspector and look at the **operation probabilities** and **target probabilities** panels:

```bash
uv run jev      # http://127.0.0.1:8766
```

- **Varied per-element numbers** → the route returns a real distribution.
- **Flat, or obviously derived from one confidence value** → you are on the chat path and `spread_probabilities()` synthesised them.

The protocol dispatch keys on the **endpoint host, not the model** (`TYPESAFE_CHOICE_HOSTS = ("typesafe.ai",)` in `model.py`). So to get measured per-element probabilities you need the choice API:

```bash
TYPESAFE_ENDPOINT=https://api.typesafe.ai/v1/systemone
TYPESAFE_MODEL=jev-latest
TYPESAFE_API_KEY=<a TypeSafe key, not an OpenRouter one>
```

This matters for §3: `docs/performance.md` numbers were taken on the choice-API path. If you re-record on the OpenRouter path, say so in the doc.

---

## 5. Rotate the OpenRouter key used for the cloud session

The key that was configured in the cloud session's environment should be rotated before this work continues, as routine hygiene for a credential that has been outside your machine: <https://openrouter.ai/keys>.

Then update `.env` locally. `.env` is gitignored — keep it that way.

---

## 6. One open design decision — not a task

Three ported modules contain working code the model **cannot reach**:

| Capability | Where | Why it is unreachable |
| --- | --- | --- |
| `hover`, `dblclick`, `drag` | `pointer.py` | no pointer operation in the action space |
| `press` for `Home`/`End`/`Backspace`/`Delete`/arrows/combos | `keyboard.py` | no key operation in the action space; supervisor `ALLOWED_KEYS` unchanged |

Exposing them means adding operations to the policy in `model.py:action_space()` and `questions.py`, which changes what the model is allowed to do. I deliberately did not widen the supervisor's `ALLOWED_KEYS` either — `Backspace`/`Delete` on a focused field are destructive, and widening LLM-driven recovery would undo part of PR #1's hardening.

Decide whether you want it; it is a policy change, not a cleanup.

---

## Appendix — what landed, and one thing worth reading

| PR | What | Source |
| --- | --- | --- |
| #1 | `CLICK_TEXT` confused-deputy fix, no-progress stop, supervisor retry loop, endpoint validation | this session |
| #2 | `waits.py` — WAIT on a network-idle signal | ego-lite port |
| #3 | `events.py` — CDP event fan-out | another session |
| #4 | `keyboard.py` — key events with correct identity | ego-lite port |
| #5 | OpenRouter unification, Jev as the policy default | another session |
| #6 | `pointer.py` — synthetic wheel | ego-lite port |

**Read #3 if you read only one.** It fixes a bug PR #2 introduced: `browser_harness.drain_events` is destructive, and both the WAIT path and `record_flights.py`'s screencast thread were draining the same shared buffer — each stealing the other's events. The recorder took `Network.*` so the wait reported idle while requests were in flight; the wait took `Page.screencastFrame` so those frames were never written or acked, and Chromium throttled the screencast. #3 replaced it with a single pump plus subscriptions, and the network subscription stores *state* (in-flight ids, last-activity time) rather than events, so no backlog can evict a request that is still pending.

That matters for §3 above: **the recording evidence produced before #3 may be incomplete.**

### Ported-from

`waits.py`, `keyboard.py`, `pointer.py` are ports from [citrolabs/ego-lite](https://github.com/citrolabs/ego-lite) (MIT), each with the source path in its module docstring. Three corrections were made to the source where Chromium disagreed with it; those are in the docstrings too.
