# TypeScript UI: where it stands and how to continue

Written 2026-10-25 while the build was still running; the manager updates it as lanes
land and finalises it at the hand-off. Read `docs/typescript-ui-design.md` first (binding
decisions and rules), then `docs/typescript-ui-handoff.md` and `docs/bottom-input-prompt.md`
(the original plans).

## Done and committed (branch alpha, not pushed)

- Milestone 1: `.tmt_ui` and `.tmt_mouse` settings, Settings and `/config` rows, `--ui` and
  `TMT_UI`, hidden `--backend-stdio`, `agent_tsui.launch` (falls back to the Python UI).
- Pinned input box and live frame in `tui/` (custom renderer, not Ink): 95 vitest tests.

## In flight when this was written

- Lane 2, headless backend: `agent_backend.py`, `docs/tui-protocol.md`, two test files,
  all uncommitted until the lane reports.

## Not started

- Wiring the TS session to the backend (`tui/src/main.ts`, backend client, event handlers
  for streaming, tool actions, approvals, questions, plan/review/verify blocks, status).
  Plan: the splash, menu, Settings and key entry stay the PYTHON screens, run as a
  delegated step before the session, so Settings (and the toggle back to Python) stay
  reachable. Only the session screen is native TypeScript.
- Screenshot run of the TS UI (ConPTY + pyte driver at `output/sim/tui_driver.py`).
- Full suite in a clean clone, then push to alpha only if green.
- Deferred to a later session: native TS splash, main menu and Settings; screen-capture
  parity against the Python UI at 80x24 and a wide size.

## Known facts

- `run_tests.py` has no options: running it runs everything (about 15 minutes, one live
  test makes real API calls). Run single modules with a small script that adds the
  `testing/` dirs to `sys.path` and calls each module's `test_*` functions.
- Failures that already existed on a clean HEAD, caused by this machine's `.tmt_context`
  being "off": `test_agent_launch.test_the_frame_draws_the_state_of_every_other_row_unchanged`
  and 7 `test_agent_context_wiring` tests when run after `test_agent_tips_wiring`.
- `pyproject.toml` lists modules by name: a new Python module (e.g. `agent_backend`) must
  be added to py-modules or `test_agent_cli` fails.
- Never touch `app/`; stage explicit paths only; do not push unless told.
