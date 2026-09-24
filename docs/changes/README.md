# Change orders

One folder per change: `docs/changes/<slug>/`.

- `spec.md` — why, what changes, acceptance, and `## Not doing`.
- `tasks.md` — `## N.` is a wave. Each task is one line: `- [ ] N.M <what> (<files>). Verify: <command>`.
  Tasks in one wave run in parallel; a wave starts only after the previous wave is fully merged.
- `runs.md` — dispatch log written by `/cc-dispatch`. Do not edit by hand.

A task PR contains its tests, pastes the `Verify:` output under `## Verify`, never edits `tasks.md`,
and fixes `spec.md` in the same PR if the spec is wrong. PR title: `<slug> N.M: <one line>`.

Tests stay offline. A task whose effect can only be proven with paid model calls ships the code plus
offline tests; the paid measurement is a human gate recorded in `spec.md`, not part of the task.
