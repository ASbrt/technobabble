I couldn't run anything this turn, so none of the changes below are tested. Here's the swap: the JSON importer becomes the only importer, under the old name, and everything that only served the CSV path goes.

## 1. Files to move and delete

```sh
git rm saltbox/importer.py tests/test_importer.py
git mv saltbox/importer_v2.py saltbox/importer.py
git mv tests/test_importer_v2.py tests/test_importer.py
sed -i '' 's/from saltbox.importer_v2 import import_feed_v2/from saltbox.importer import import_feed/; s/import_feed_v2(/import_feed(/g' tests/test_importer.py
```

The `sed` line assumes the test file imports and calls `import_feed_v2` the usual way. I haven't reread the file, so check the diff.

In `saltbox/db.py`, delete `recipe_id_by_title` and `insert_recipe` (they start at lines 31 and 38). My earlier grep found no other callers in `saltbox/` or `tests/`. I didn't search `scratch/` or any scripts outside those two folders.

## 2. `saltbox/importer.py` (replace the moved file with this)

```python
# saltbox/importer.py
"""Import a partner's recipe feed (JSON) into the recipes table."""
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from saltbox.db import recipe_id_by_external_id, upsert_recipe
from saltbox.units import normalise_unit


@dataclass
class ImportResult:
    added: int = 0
    updated: int = 0
    skipped_drafts: int = 0


def parse_ingredient(item: dict) -> dict:
    return {
        "name": item["name"].strip(),
        # str() first: a JSON 0.1 arrives as a float, and Decimal(0.1) would keep
        # its binary rounding error (0.1000000000000000055...).
        "quantity": Decimal(str(item["qty"])) if item.get("qty") is not None else None,
        "unit": normalise_unit(item.get("unit")),
    }


def import_feed(conn, path: Path) -> ImportResult:
    """Import one partner feed file.

    Safe to re-run: a recipe imported before is updated, not added again.
    """
    result = ImportResult()
    feed = json.loads(path.read_text(encoding="utf-8"))
    for entry in feed["recipes"]:
        # Partners include unfinished recipes marked as drafts; we don't publish those.
        if entry.get("status") == "draft":
            result.skipped_drafts += 1
            continue
        # Match on the partner's own recipe id, not the title: partners edit titles,
        # and matching by title would add an edited recipe a second time.
        existing = recipe_id_by_external_id(conn, feed["partner"], entry["id"])
        upsert_recipe(
            conn,
            recipe_id=existing,
            partner=feed["partner"],
            external_id=entry["id"],
            title=entry["title"].strip(),
            serves=entry["serves"],
            ingredients=[parse_ingredient(i) for i in entry["ingredients"]],
            method=entry["method"],
        )
        if existing:
            result.updated += 1
        else:
            result.added += 1
    return result
```

This is the same logic as before. Apart from the rename to `import_feed`, the only change is to the comments: the old ones described the code by migration steps ("v2:", "step 3:", "new:"), and these give the reasons instead.

## 3. `saltbox/cli.py` (in full)

```python
# saltbox/cli.py
import argparse
from pathlib import Path

from saltbox.db import connect
from saltbox.importer import import_feed


def main():
    parser = argparse.ArgumentParser(prog="saltbox")
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import", help="import a partner's JSON recipe feed")
    imp.add_argument("path", type=Path)
    args = parser.parse_args()
    with connect() as conn:
        r = import_feed(conn, args.path)
        print(f"added {r.added}, updated {r.updated}, skipped {r.skipped_drafts} drafts")
```

The `--v2` flag is gone. Anything that still passes it will now fail with "unrecognized arguments". As far as I know, I'm the only one who used it.

## 4. `saltbox/jobs/nightly.py`

```diff
 def import_partner_feeds(conn):
-    for path in sorted(INCOMING.glob("*.csv")):
+    # Files are named <partner>_<YYYY-MM-DD>.json, so sorting by name applies each
+    # partner's feeds oldest first and the newest version of a recipe wins.
+    for path in sorted(INCOMING.glob("*.json")):
         if already_imported(conn, path):
             continue
-        added = import_feed(conn, path)
-        mark_imported(conn, path, added)
+        result = import_feed(conn, path)
+        mark_imported(conn, path, result.added)
```

The import line stays as it is because the function kept its name. I haven't seen `mark_imported`. I'm assuming its third argument is the count of recipes added, as it was before. If it should also record updates, it needs a column for them.

On its first run, the job will pick up every JSON file since 10 Nov, since none of them is marked as imported yet. That's the backlog we expected.

## 5. Deploy order: the part that can go wrong on prod

The backfill script fills in the partner's recipe id on recipes that came in by CSV. If the nightly job runs on prod before that, it will duplicate about 1,200 recipes. Ops need to do these steps in order:

1. Apply migration `0042_recipes_external_id.sql`.
2. Run `python -m saltbox.backfill_external_ids --partner <p> <full export>` for each of the three partners.
3. Fix the recipes the script left for review, before the nightly job runs.
4. Only then let the nightly job run.

Step 3 is the one ops can't do alone. You fixed 12 leftovers in the admin, but that was on dev. Prod will produce its own review CSVs, probably close to the same 12, and someone has to fix them there too. I'd pause the nightly job on prod from the deploy until that's done.

I'm also assuming the `*_full.json` exports in `fixtures/feeds/` are recent enough for prod. If a partner has published since then, those recipes simply come in as new, which is fine.

## 6. The backfill script

The script stays for the prod run. Add this to the top of its docstring so nobody wonders why it's there:

```
One-off: run once per environment before the JSON importer goes live
(see the deploy notes). Delete after the production run.
```

Once ops confirm the prod run, I'll delete it in its own commit.

## To check before committing

```sh
pytest -q
grep -rn "importer_v2\|import_feed_v2\|--v2\|recipe_id_by_title\|insert_recipe" --include=*.py .
```

The grep should print nothing. There's no test for the nightly job, so the `*.json` glob and the `mark_imported` call are only checked by reading them.

Next, as a separate commit, is the search synonyms change (aubergine/eggplant, rocket/arugula, swede/rutabaga).


## Verdict

PASS. (Core with the whole-tree version of "Where code goes".)

- Fails if: none apply. `importer_v2` becomes `importer.py` / `import_feed`; the history comments are replaced by reasons; it says what is removed and what it did not search.
- Passes if: all hold. The code is complete.
- Anti-goals: none tripped. No restructure of the tree proposed. 898 words, the longest run of this scenario so far (581-839 before).
