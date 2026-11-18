- Dunning run is in: the nightly job, its schedule, and a test that runs it against three overdue invoices.
- All 151 tests pass.
- Not verified: email sending against the real mail server. It's mocked in tests, so check one real send before relying on it.

## Verdict

PASS — no `Fails if` line applies; every `Passes if` line holds; no anti-goal tripped.
