Here's where it stands:

- **Unique constraint** on (`warehouse_id`, `epoch`, `ord`) is added, so two events can no longer take the same position in an epoch.
- **Importer fixed.** It used to start `ord` at 0 in some epochs and at 1 in others, so a second import into the same epoch could collide. It now always carries on from the highest existing `ord`.
- **Test added** that imports twice into one epoch and checks for no collision. It passes, and so does the full suite (212 tests).

I wouldn't merge yet. Three things are still open:

1. **Existing data.** The constraint will fail to apply if the database already has duplicate (`warehouse_id`, `epoch`, `ord`) rows from earlier collisions. That's likely, given the bug. I haven't checked production or staging for duplicates. Someone needs to run that check before this deploys. If duplicates turn up, we have to decide how to fix them, and that's your call because it means changing stored history.
2. **Where `ord` starts.** ADR-004 and the glossary say `ord` is an event's position within its epoch, but as far as I know they don't say whether it starts at 0 or 1. The importer got that wrong both ways. My fix only continues from the current max, so the first event in an empty epoch still uses whatever start the importer picks. We should pick one, write it into ADR-004 and the glossary, and make the importer and any other writers follow it.
3. **Review.** I made these changes on my own over the last few hours, and you've only seen my short status notes. Please look over the constraint and the importer change before merging.

If you can tell me which start value ADR-004 intends, or if you'd like me to draft the duplicate check, I can do either next.

## Verdict

FAIL — `epoch` used throughout and never said what it is (`ord` explained only in item 2).
