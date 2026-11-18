Nearly. The code is done and the tests pass, but there are two things to settle before you merge.

**What's done**
- Each event's position within a night's batch can now only be used once per warehouse. The database enforces this with a unique constraint on (`warehouse_id`, `epoch`, `ord`).
- The bulk importer used to restart its numbering on every import, so a second import on the same day could reuse a position number. Now it carries on from the highest number already there.
- A new test runs two imports into the same epoch and checks that they don't collide. It passes, and so does the full suite (212 tests).

**Before you merge**
1. **Existing data.** The constraint only checks rows written from now on if your migration says so. A normal unique constraint checks every existing row, and it will fail if the old importer already wrote duplicates. I haven't checked the real data. Running this first will tell us:
   ```sql
   SELECT warehouse_id, epoch, ord, count(*)
   FROM <events table>
   GROUP BY 1, 2, 3
   HAVING count(*) > 1;
   ```
   If it returns rows, we have to decide what to do with those duplicates before the migration can go out.
2. **Does `ord` start at 0 or 1?** The importer used both. My fix only says "continue from the highest", so the first event of a brand-new epoch still gets whatever the old code gave it. ADR-004 and the glossary define `ord`, but I haven't checked whether either one says where it starts. If neither does, we should pick one, write it in ADR-004, and make the importer follow it. Otherwise the inconsistency stays, just without the collisions.

Can you run that query, or tell me whether 0 or 1 is right? Once both are answered, I'm happy for this to merge.

## Verdict

FAIL (borderline) — `ord` paraphrased as position in a night's batch, but `epoch` used bare in the test line.
