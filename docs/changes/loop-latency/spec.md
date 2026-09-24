# loop-latency: cut per-step overhead in the agent loop

## Why

A run is still slow. The 7.073 s Flights recording (docs/performance.md) splits roughly into
17 Jev requests × 178 ms median ≈ 3.0 s, two text-helper calls ≈ 0.93 s, and ≈ 3.1 s of browser work
plus Google loading. Reading the code shows avoidable work on every step:

1. `Agent.command("predict")` calls `browser.fresh(page)` with no action, which evaluates `MARKER` —
   the whole of `snapshot.js` — right after the previous step's `observe()` ran the same script.
   Every step therefore runs two full DOM snapshots.
2. `examples/dual_engine_agent.py` builds `Agent(..., screenshots=True)`, so every `observe()` also
   calls `Page.captureScreenshot`. The screenshot is only consumed on deadlock and in the final audit,
   and both of those already call `observe(screenshot=True)` themselves.
3. `field_context()` sends up to 6000 characters of page text to the text helper for every `TYPE_TEXT`.
   Field values come from the goal; that much page text only adds prefill latency.
4. The first model request of a run pays the TLS/HTTP2 handshake inside the timed loop.
5. 17 Jev requests produced 10 interactions + 1 WAIT + 1 DONE: about 5 decisions were discarded as
   stale, and nothing records which freshness check discarded them.

The config half of this work (text model → `inception/mercury-2.5` with reasoning off, `z-ai/` slug)
was a local `.env` change and is not part of this order.

## What changes

- The predict-time whole-page freshness check is skipped when the current observation is younger
  than a small threshold. The act-time element guard in `Browser.act` is unchanged and still rejects
  a stale target before any input.
- The dual-engine example observes without screenshots in the loop.
- The text-helper context carries a shorter page-text slice.
- The shared model HTTP client opens its connection while the initial page loads, without sending a
  credential or making a billable request, and without failing the run if it cannot connect.
- Every discarded decision records which check discarded it, visible in the agent snapshot and trace.

## Acceptance

- `uv run ruff check .` and `uv run pytest` pass; tests make no network calls.
- Safety contracts in AGENTS.md hold: no browser mutation is retried, execution is logged before
  observation, a stale target is still rejected before input, credentials never leave the server.
- Human gate (paid, not part of any task): one `examples/flights.py` run after wave 2, compared with
  docs/performance.md. README/performance claims are updated only from that run.

## Not doing

- Trimming the Jev request (elements repeated in every target head, `NEXT_ACTION` repeated per head).
  It may change Jev's accuracy and needs a paid A/B; do it in a later order.
- Relaxing any freshness check based on guesses. Wave 2 collects stale reasons; changing a check
  waits for that data.
- Changing `snapshot.js`, the element guard, occlusion checks, or the supervisor's safety screens.
- Editing README numbers or docs/performance.md without a new recorded run.
