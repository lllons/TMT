# Tests

```bash
python run_tests.py
```

The suite lives in `testing/`, split into `testing/unit/` and `testing/integration/`.
The runner stays at the root and discovers both; see
[testing/README.md](../testing/README.md) for what belongs where.

1581 tests. Eight of them read the API key from `.tmt_key`, so on a fresh clone with
no key configured those eight fail and the rest pass.

It takes roughly fifteen minutes rather than the two it used to, and almost all of that
is one test in `test_agent_review.py` that starts three real reviewer agents and waits
out a live API round trip for each. That one is also the only test here that is not
deterministic: it settles a failing review and then sends bogus objects to prove none of
them can turn it into a pass — but running `review` runs a review, so a live reviewer
that likes your working tree passes it through the legitimate route and the assertion
fires. Re-run it before believing it.

---

[← Back to the README](../README.md)
