Here's where it stands:

**Done**
- Unique constraint on (`warehouse_id`, `epoch`, `ord`) is in.
- The bulk importer no longer restarts `ord` for each batch. It continues from the highest `ord` already in the epoch, so a second import into the same epoch no longer collides.
- New test: two imports into one epoch, no collision. It passes, and so does the full suite (212 tests).

**Before you merge, three things I haven't checked:**

1. **Existing data.** If any live database already has duplicate (`warehouse_id`, `epoch`, `ord`) rows from the old importer, adding the constraint will fail on deploy. I only ran it against the test database. Run a quick `GROUP BY warehouse_id, epoch, ord HAVING count(*) > 1` on production (or a recent copy) first. If it finds rows, we need a cleanup step before the constraint.
2. **Does `ord` start at 0 or 1?** The old importer did both. I made it continue from the max, but I didn't check which starting value ADR-004 and the glossary specify for a fresh epoch. If they name one, the importer should use it. If they don't, the ADR should say so.
3. **Concurrent imports.** "max(ord) + 1" can still race when two imports run into the same epoch at the same moment. The constraint now makes the second one fail loudly instead of quietly writing duplicates, which is safe but not graceful. If imports can overlap, we'd want a lock or a retry. If they never overlap, this is fine as is.

Also, the new constraint is a rule about event ordering, so it should be mentioned in ADR-004 next to the `epoch`/`ord` definitions. I haven't updated the ADR yet.

Short answer: the code works and is tested, but I wouldn't merge until (1) is checked. Want me to write the duplicate check and the ADR-004 note now?

## Verdict

FAIL — neither `ord` nor `epoch` explained.
