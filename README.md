<img src="docs/banner.svg" alt="Jev Ultrafast · Browser Use × TypeSafe" width="100%" />

# Jev Ultrafast ⚡

> [!IMPORTANT]
> **The Browser Use Cloud waitlist is open.** Get early access to ultrafast browser agents in the cloud.
> **[Join the waitlist →](https://browser-use.com/ultrafast?utm_source=github&utm_medium=readme&utm_campaign=jev-ultrafast)**

**A browser agent with a dynamic, indexed action space.**

Give it one goal. [TypeSafe's Jev](https://docs.typesafe.ai/introduction) picks an operation and an element. A small LLM writes text only when the operation is `TYPE_TEXT`.

**Zürich → London on Google Flights in 7.1 seconds.** One natural-language goal, actual text generation, and loading waits included.

<a href="docs/demo.mp4"><img src="docs/demo.gif" alt="A real Google Flights search at 1× speed, with generated city names and dynamic operation/target decisions" width="100%" /></a>

[Watch the MP4](docs/demo.mp4) · [Measurements](docs/performance.md) · [Read the loop](jev_ultrafast/agent.py)

## The action space

Every observation produces a new element table:

```text
[1] button    Change ticket type · Round trip
[2] combobox  Where from?        · San Francisco
[3] combobox  Where to?          · empty
[4] textbox   Departure          · empty
...
```

The operations are `CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_UP`, `SCROLL_DOWN`, `WAIT`, `DONE`, and `BLOCKED`. Only supported operations and targets are offered.

```text
                      one TypeSafe request
                     ┌───────────────────────────┐
page → element table → operation                 │
                     │ click_target              │
                     │ type_text_target          │
                     │ select_target, if present │
                     └─────────────┬─────────────┘
                         use the matching target
                                   │
                    CLICK [7] ─────┤──→ browser
                TYPE_TEXT [3] ─────┘
                          ↓
                   small LLM → text → browser
```

Target questions are speculative. If the operation is `CLICK`, only `click_target` can execute. Two decisions, **one network round trip**. Each target head contains only compatible elements. Native dropdown choices carry an observed element/option index.

There are no site-specific action scripts or prepared field strings in the policy. The Flights example supplies a goal and independently verifies the outcome. The screenshot renderer adds labels afterward; it does not drive the browser.

## Try it

```bash
git clone https://github.com/browser-use/jev-ultrafast.git
cd jev-ultrafast
uv sync
cp .env.example .env
# Paste one OpenRouter key into the three *_API_KEY placeholders.
uv run jev
```

Open **http://127.0.0.1:8766** and click **Start demo → Run automatically**. The inspector shows numbered elements, operation probabilities, target probabilities, and executed actions. **Choose next** pauses before execution.

Chrome connects through [Browser Harness](https://github.com/browser-use/browser-harness), installed by `uv sync`. Run `uv run browser-harness --doctor` if it needs connecting. Allow remote debugging in Chrome when prompted.

### Providers

[.env.example](.env.example) ships one OpenRouter key driving all three models — Jev picks the operation and target, and a small chat model writes field values and audits the result — and those values are also the built-in defaults, so unset variables match the documented setup.

| Variable | Job | Default |
| --- | --- | --- |
| `TYPESAFE_API_KEY` / `TYPESAFE_ENDPOINT` / `TYPESAFE_MODEL` | Picks the operation and target each step | `https://openrouter.ai/api/v1`, `typesafe/jev-1.13` |
| `TEXT_MODEL_API_KEY` / `TEXT_MODEL_BASE_URL` / `TEXT_MODEL` | Writes field values for `TYPE_TEXT` | same endpoint, `z-ai/glm-5.3-flash` |
| `VISION_MODEL_API_KEY` / `VISION_MODEL_BASE_URL` / `VISION_MODEL` | Visual supervisor, falls back to the text settings | same |

The policy speaks two protocols. Against `typesafe.ai` it uses TypeSafe's constrained choice API, which returns a real probability distribution over the offered ids. Against any other endpoint it asks one OpenAI-compatible chat model to answer every question in a single JSON reply; that model reports one choice and one confidence per question, so the distribution shown in the inspector is spread from that confidence rather than measured per element. Both paths keep one request per decision cycle, and both validate every answer against the observed element ids before anything executes — a key the page never offered is rejected, not clamped. The shipped configuration runs Jev over OpenRouter, so the policy is the same model either way and only the protocol differs. Set `TYPESAFE_ENDPOINT=https://api.typesafe.ai/v1/systemone` with `TYPESAFE_MODEL=jev-latest` to reach it through the choice API and get measured per-element probabilities; the measurements below were taken on that path.

OpenRouter, Zhipu, Gemini, and DeepSeek all work for the chat path; set the matching model, endpoint, and `TYPESAFE_MODEL_REASONING` / `TEXT_MODEL_REASONING`. Every endpoint must be `http://` or `https://` — each one carries a bearer token, so an unvalidated setting is rejected before the request.

## Use the library

```python
from jev_ultrafast import Agent

with Agent(
    "https://www.google.com/travel/flights?hl=en",
    "Find one-way flights from Zurich to London on September 20, 2026, "
    "for one adult in economy. Stop when matching flight options are visible.",
) as agent:
    for state in agent.run():
        print(state["elapsed_ms"], state["status"])
```

Run with `uv run --env-file .env python your_script.py`. The same policy can run a different task:

```bash
uv run --env-file .env python examples/run.py \
  --url https://en.wikipedia.org/wiki/Main_Page \
  --goal 'Find and open the Wikipedia article about Gödel’s incompleteness theorems.'
```

`uv run --env-file .env python examples/flights.py --keep-open` performs the flight search, checks the actual route/date/results, and saves its trace. It does not select or book a flight.

### Dual-Engine Agent

Combines the atomic reflex loop with a second model acting as both text helper and multimodal visual supervisor. The shipped configuration runs all three on one OpenRouter key, with Jev as System 1; set `TYPESAFE_ENDPOINT` as above to reach Jev through TypeSafe's choice API instead. The banner printed at startup names the models actually in use:

```bash
uv run --env-file .env python examples/dual_engine_agent.py \
  --url "https://www.google.com/travel/flights?hl=en" \
  --goal "Find one-way flights from Zurich to London on September 20, 2026 for one adult in economy."
```

#### Architectural Security, Fork Deviations & Privacy

In upstream Jev, model output strictly never becomes coordinates or executable JavaScript. This fork introduces **System 2 (Visual Supervisor)** as an out-of-band diagnostic channel active only when System 1 reaches deadlock (`BLOCKED`).

To strictly defend against prompt injection and confused deputy attacks while retaining emergency recovery capabilities:
1. **Strict Action Whitelist**: Only `CLICK_TEXT`, `PRESS_KEY`, `SCROLL`, and `RELOAD` are accepted (`ALLOWED_ACTIONS`).
2. **Bilingual Safe Click Patterns**: Button texts are constrained to predefined dismiss/consent patterns (`SAFE_CLICK_EN` with `\b` word boundary; `SAFE_CLICK_ZH` matching Chinese consent patterns like `同意並繼續`, `全部接受`, `我同意`).
3. **Two-Stage Dangerous Keywords Blacklist**: Text containing `delete`, `pay`, `checkout`, `buy`, `transfer`, `logout` (and Chinese equivalents `刪除`, `付款`, `結帳`, `轉帳`, `登出`) is unconditionally rejected before pattern matching. The same screen runs **twice**: once on the label the model proposes, and again on the text of the element the DOM actually resolved. Resolution is anchored at the start of the label, so a safe prefix cannot stand in for an unsafe control (target `ok` never reaches `Book now`).
4. **Viewport Bounds & DOM Stability**: `CLICK_TEXT` coordinates are strictly bounded within viewport dimensions (`r.top < window.innerHeight && r.left < window.innerWidth`), and `_settle` polls engine-maintained counters (`readyState`, element count, scroll height, title) for DOM stability before resuming the reflex loop, without serialising the document.
5. **Fail-Closed Outcome Verification**: Visual audits require `confidence >= 0.70` and non-`done` runs exit with deterministic non-zero codes (code 2 for loop failure, 3 for missing audit, 1 for unverified audit).
6. **Privacy & Cost Controls**: Multimodal diagnostics transmit quality=72 JPEG screenshots to `VISION_MODEL_BASE_URL`. A maximum retry cap (`max_supervisor_retries=3`) limits session spend, and visual supervision can be completely turned off via `VISION_SUPERVISOR_ENABLED=false` for sensitive or authenticated sessions.

## Why it moves

- **One request per decision cycle.** Operation and target heads share the same observed state.
- **No screenshots in the default agent loop.** Jev consumes structured state. The inspector opts into screenshots; the video uses a separate continuous screencast.
- **One browser call per snapshot.** Read visible controls, their names, values, and text atomically. Keep references to the actual DOM nodes.
- **Validate the selected target.** Clicks check the document, form values, target, and nearby context. Animation alone does not force another prediction. Resolve current geometry and reject covered controls before input.
- **Wait for useful state.** After typing into a combobox, wait for visible suggestions, capped at 200 ms. Other interactions get at most two animation frames or 50 ms. These reads happen after execution is logged.
- **Spend a WAIT on a signal, not a sleep.** A `WAIT` costs a decision, so it returns the moment the session's network goes quiet (250 ms with nothing in flight, 2 s ceiling) instead of sleeping a fixed slice and paying another decision to look again. The Network domain is enabled once per session, so a wait adds no protocol calls of its own.
- **Keep hidden tabs rendering.** Focus emulation prevents background animation throttling without switching Chrome's visible tab.
- **Send visible text.** Offscreen article bodies and footers do not fill the model context.
- **Reuse an interrupted text request.** A generated value survives a stale-page retry only if the entire text-helper input is unchanged.

A `SCROLL_UP` or `SCROLL_DOWN` dispatches a real `WheelEvent` at the viewport centre and performs the scroll, rather than a CDP `mouseWheel`. The agent owns a background target, and in this container's Chromium the first CDP wheel of a session reached the page once in six tries; the synthetic path reached it every time, costs the same one call, and reports whether the page actually moved. A page that calls `preventDefault()` on the wheel keeps the page in place, as a real wheel would. Nested scrollers stay outside this MVP.

Replacing a field's text presses the platform select-all accelerator with its real key identity and the `selectAll` editing command, so a page that inspects the key event sees `keyCode` 65 rather than 0, and the selection still happens if the page swallows the event. Key dispatch is code-owned: the operation policy has no key operation, and the visual supervisor may only send the keys in its allowlist.

Every executed target is resolved from an observed node. The executor rechecks page freshness and click occlusion. Model output never becomes selectors, coordinates, shell commands, or executable JavaScript. Text-helper output must parse as a small JSON object before typing. When the helper answers `{"text": null}` because the goal supplies no value, the run stops as `BLOCKED` instead of typing a guess.

## Small enough to read

| File | Job |
| --- | --- |
| [agent.py](jev_ultrafast/agent.py) | The complete loop and text-helper handoff |
| [supervisor.py](jev_ultrafast/supervisor.py) | Multimodal visual diagnosis, deadlock recovery, and goal verification |
| [snapshot.js](jev_ultrafast/snapshot.js) | Atomic DOM snapshot, indexed controls, freshness guards |
| [browser.py](jev_ultrafast/browser.py) | Browser connection, current geometry, execution |
| [waits.py](jev_ultrafast/waits.py) | Document, network-idle, and predicate waits over the owned session |
| [keyboard.py](jev_ultrafast/keyboard.py) | Key events with the identity a page expects, and editor commands |
| [pointer.py](jev_ultrafast/pointer.py) | Click, wheel, hover and drag input over the owned session |
| [model.py](jev_ultrafast/model.py) | Dynamic operation/target heads and text generation |
| [questions.py](jev_ultrafast/questions.py) | Model instructions |
| [demo.py](jev_ultrafast/demo.py) | Local inspector |
| [dual_engine_agent.py](examples/dual_engine_agent.py) | End-to-end dual-engine runner combining the reflex loop with visual supervision |

## Evidence and limits

The recorded runs below predate the network-idle `WAIT`; each contains one `WAIT` action that cost a fixed 100 ms at the time. Re-recording needs a real Chrome and paid API calls.

The current video is a **7,073 ms** Google Flights run. Timing starts after initial page observation and includes model calls, generated text, browser work, stale decisions, and loading waits. A fresh independent check verifies the one-way setting, Zürich, London, September 20, 2026, and visible flight options. The video plays at 1×, with no opening hold and a 0.5-second final hold.

In six alternating runs with identical models and settings, both versions passed **3/3**. Median task time went from **9.450 s → 7.092 s**, a **25% reduction**; median browser protocol calls went from **1,092 → 101**. This is three repeats of one task on one browser profile, not a general reliability benchmark.

The same policy opened the requested Wikipedia article in **2.798 s** and passed a local hotel search/filter task in **1.896 s**. Runs, failures, source hashes, and measurement boundaries are in [performance.md](docs/performance.md).

A `DONE` choice still requires independent outcome verification. The DOM reader handles common HTML and ARIA controls, not the full accessible-name specification. Shadow roots, frames, canvas, uploads, pop-up tabs, nested scrolling, and arbitrary keyboard widgets remain outside this MVP. Owned tabs share the existing Chrome profile.

## Development

```bash
uv run ruff check .
uv run pytest
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/snapshot.js
uv build
```

Tests are offline. `uv run python scripts/check_guards.py` checks real controls in a local browser without model calls. `uv run --env-file .env python scripts/probe_provider.py` goes the other way: it calls the configured policy, text, and vision endpoints once each — no browser — so a wrong key, endpoint, or model id surfaces before a live run. Live examples, the probe, and recording scripts make paid API calls. `scripts/record_flights.py <new-folder>` captures original browser timestamps; `scripts/render_demo.py <recording-folder>` renders that verified run at 1× and crops out the Google account strip. Credentials and raw traces stay ignored.

---

[Browser Use](https://github.com/browser-use/browser-use) · [Browser Harness](https://github.com/browser-use/browser-harness) · [TypeSafe speculative fan-out](https://docs.typesafe.ai/patterns/fan-out)
