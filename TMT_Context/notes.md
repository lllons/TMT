# TMT Project Notes

<!-- TMT_Context format version: 1 -->

## Project Overview

`TMT` -- a python project, detected from `pyproject.toml`, `run_tests.py`.

From `README.md`:

> Installing puts one command on your PATH:

## Architecture

Top-level directories:

- `assets/`
- `logs/`
- `output/`
- `testing/`
- `tmtcode.egg-info/`

Entry points this project declares in its own manifest:

- `tmtcode` runs `TMT:main` (declared in `pyproject.toml` under `[project.scripts]`)

## Important Files

Files TMT recognised at the top level:

- `pyproject.toml`
- `run_tests.py`
- `README.md` -- documentation

## Build

Build command has not yet been confirmed. Nothing in this repository names one that TMT recognised.

## Testing

Test command: `python run_tests.py`

Chosen because this repository has run_tests.py in its root, which is how it runs its own suite.
It cannot be narrowed to specific paths, so the whole suite is the only test evidence available.

Tests live in `testing/`.

## Configuration

Environment: system python.

## Dependencies

Declared in `pyproject.toml`. That file is the current list; this note deliberately does not copy it, because a copy goes stale the next time anything is installed.

## Constraints

Not yet recorded.

## Known Issues

Not yet recorded.

## TMT Notes

Written by TMT on its first task in this project, from reading the repository. Nothing here was run and nothing was inferred beyond what the files say.

Anything marked "not yet confirmed" is genuinely unknown rather than assumed. Correct it as you find out -- this file is meant to be edited, by TMT and by hand.
