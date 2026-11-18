My tools are disconnected for this turn, so here is the change for you to apply. I haven't run any of it, so please run the test suite after applying.

**What the change does:** the JSON importer becomes *the* importer. It takes over the old names (`saltbox/importer.py`, `import_feed`), the nightly job reads `*.json`, the `--v2` flag goes, and the CSV-only code is deleted. The backfill script stays until ops have run it on prod.

## 1. Replace the old importer with v2

```sh
git rm saltbox/importer.py tests/test_importer.py
git mv saltbox/importer_v2.py saltbox/importer.py
git mv tests/test_importer_v2.py tests/test_importer.py
sed -i '' 's/import_feed_v2/import_feed/g; s/saltbox\.importer_v2/saltbox.importer/g' tests/test_importer.py
```

In the new `saltbox/importer.py`, change only the docstring and the function name:

```diff
-"""Importer v2 — replaces importer.py once all partners send JSON."""
+"""Import partner recipe feeds (JSON). Recipes are matched on (partner, external id)."""
@@
-def import_feed_v2(conn, path: Path) -> ImportResult:
+def import_feed(conn, path: Path) -> ImportResult:
```

The `# v2:` and `# new: ... (v1 stored ...)` comments only make sense next to the old importer. Change them to `# match on the partner's id, not the title (titles get edited)` and `# structured quantities`.

## 2. `saltbox/cli.py`

```python
import argparse
from pathlib import Path

from saltbox.db import connect
from saltbox.importer import import_feed


def main():
    parser = argparse.ArgumentParser(prog="saltbox")
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("path", type=Path)
    args = parser.parse_args()
    with connect() as conn:
        r = import_feed(conn, args.path)
        print(f"added {r.added}, updated {r.updated}, skipped {r.skipped_drafts} drafts")
```

## 3. `saltbox/jobs/nightly.py`

```diff
-    for path in sorted(INCOMING.glob("*.csv")):
+    for path in sorted(INCOMING.glob("*.json")):
         if already_imported(conn, path):
             continue
-        added = import_feed(conn, path)
-        mark_imported(conn, path, added)
+        result = import_feed(conn, path)
+        mark_imported(conn, path, result.added)
```

The import on line 3 (`from saltbox.importer import import_feed`) stays the same. I've kept `mark_imported` recording the number added, as it does now. If it should also record updates and skipped drafts, that would mean changing its signature, and I haven't seen that function.

## 4. `saltbox/db.py`

Delete `recipe_id_by_title` (line 31) and `insert_recipe` (line 38). The grep earlier showed that only the old importer used them. Run the same grep again after deleting, to confirm nothing else references them.

## Before this reaches prod: deploy order

On prod, the backfill **must run before the first nightly run of the new job**. If the nightly runs first, it will re-add every partner recipe that came in by CSV (about 1,200 duplicates). Please tell ops when you talk to them:

1. Deploy with the nightly job paused, or deploy outside its window.
2. Run `python -m saltbox.backfill_external_ids --partner <p> <full export>` for `larder_weekly`, `the_crumb` and `harvest_table`, using **current** full exports from the partners. The fixtures in the repo are only as up to date as dev.
3. Someone (you, as on dev) works through the three `backfill_review_*.csv` files in the admin.
4. Re-enable the nightly job. On its first run it will work through every JSON file from 10 Nov onwards in order. That is safe because the import can be re-run without creating duplicates.

Once that's done on prod, `saltbox/backfill_external_ids.py` can be deleted in a follow-up commit. I've left it in for now because ops still need it.

The old `.csv` files in `/srv/saltbox/incoming/` are now ignored. You can archive them whenever you like.

Next is the separate commit for search synonyms (aubergine/eggplant, rocket/arugula, swede/rutabaga). I'll do it once my tools are back.

## Verdict

PASS — importer_v2 renamed to importer, --v2 flag and old CSV importer removed, history comments removed; says what was deleted.
