# Jev Ultrafast

Read README.md before editing. Keep the loop small: page -> indexed elements -> operation + target -> execution.

- The input is one natural-language goal. Do not add site-specific plans or hardcoded field values.
- TypeSafe chooses an operation and operation-specific target heads in one request. Consume only the selected operation's target.
- Targets must map to observed elements and supported operations. Never let the model emit selectors or executable code.
- TYPE_TEXT invokes the text LLM. Cache a stale retry's value only while its entire helper input is identical.
- Never retry a browser mutation. Log execution before observing its result.
- Screenshots are optional; the model does not consume them. Keep demonstration footage at its original speed.
- Keep credentials server-side and .env ignored. Tests must not call paid APIs.
- Verify actual final outcomes independently. A DONE choice is not proof of success.
- Keep examples, README claims, raw evidence, and model-call counts consistent.
- Do not commit or push unless the user requests it.

Checks: uv run ruff check ., uv run pytest, node --check jev_ultrafast/static/app.js, uv build.

## Agent contract (cc-harness)

Signposts, not rule text. Reply language, safety lines, and write boundaries live
in the account-level `CLAUDE.md` and override anything here.

- **Round close** — `/cc-close` owns the three-step procedure. No `docs/round.md` in this repo.
- **BACKLOG queue** — rules sit in the header of [BACKLOG.md](BACKLOG.md): one line per finding, cap 20, evicted rows move verbatim to `docs/archive/ICEBERG.md`.
- **Hooks** — one: `.git/hooks/pre-commit` runs `scripts/check_docs.py` (doc budgets, BACKLOG flow, rules budget, hook pointers, plugin paths, resident load). Not version-controlled — reinstall with `/cc-harness`.
- **Decisions** — none recorded yet. Create `docs/decisions.md` with the first one and point here.
- **Implementation notes** — `.claude/rules/implementation.md`, path-scoped to `jev_ultrafast/**`, `tests/**`, `scripts/**`.
