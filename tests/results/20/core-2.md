Here is the swap. The new logic moves into `saltbox/importer.py` under the old name `import_feed`, so there's no "v2" left anywhere. I've written all of this without running anything (my tools are off for this turn), so none of it is tested yet. The commands to check it are at the end.

Before the code, one thing about prod that changes the deploy order:

**On prod, the backfill and your manual fixes have to happen before the first nightly run.** Two reasons:

1. The fixes you made for the 12 leftovers were made in the dev admin, so prod doesn't have them. On prod the backfill will write the same three review CSVs again, and those recipes need fixing in the prod admin as well. Any you skip will be added a second time on the first run.
2. Once this is deployed, the nightly job will pick up every JSON file since 10 Nov, because it has never marked any of them as imported. If it runs before the backfill, it duplicates all ~1,200 partner recipes.

So the order for ops is: migration 0042, then the backfill for all three partners, then you fix the review CSVs in the prod admin, then the nightly job is allowed to run. If the deploy can't fit around the nightly schedule, pause the job until the fixes are done. Please pass that on along with the backfill step.

### 1. `saltbox/importer.py`: replace the whole file

```python
# saltbox/importer.py
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
        "quantity": Decimal(str(item["qty"])) if item.get("qty") is not None else None,
        "unit": normalise_unit(item.get("unit")),
    }


def import_feed(conn, path: Path) -> ImportResult:
    """Import a partner's JSON recipe feed.

    Re-importing the same feed is safe: it updates recipes instead of duplicating them.
    """
    result = ImportResult()
    feed = json.loads(path.read_text(encoding="utf-8"))
    for entry in feed["recipes"]:
        if entry.get("status") == "draft":
            result.skipped_drafts += 1
            continue
        # Match on the partner's own id, not the title: partners edit titles,
        # and matching on title turned every edit into a duplicate recipe.
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

### 2. `saltbox/cli.py`: replace the whole file (the `--v2` flag is gone)

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

### 3. `saltbox/jobs/nightly.py`: pick up `.json` files

The import on line 3 stays as it is, because the function keeps the name `import_feed`. Only the loop changes:

```diff
 def import_partner_feeds(conn):
-    for path in sorted(INCOMING.glob("*.csv")):
+    for path in sorted(INCOMING.glob("*.json")):
         if already_imported(conn, path):
             continue
-        added = import_feed(conn, path)
-        mark_imported(conn, path, added)
+        result = import_feed(conn, path)
+        mark_imported(conn, path, result.added)
```

I've only seen lines 8–16 of that file, and I'm assuming `mark_imported` still expects a count of added recipes. If it records more than that, it could take the whole `result`.

### 4. Delete and rename

```
git rm saltbox/importer_v2.py tests/test_importer.py
git mv tests/test_importer_v2.py tests/test_importer.py
sed -i '' 's/from saltbox.importer_v2 import import_feed_v2/from saltbox.importer import import_feed/; s/import_feed_v2/import_feed/g' tests/test_importer.py
```

In `saltbox/db.py`, delete `recipe_id_by_title` (line 31) and `insert_recipe` (line 38). My earlier grep found no other callers in `saltbox/` or `tests/`. It didn't look in `scratch/`, though, and `scratch/match_titles.py` might use `recipe_id_by_title`. If it does, delete that script too: it was only for the one-off title comparison.

**Kept on purpose:** `saltbox/backfill_external_ids.py` stays until ops have run it on prod. After that it can go in a small follow-up commit.

### To check it

```
pytest -q
grep -rn "importer_v2\|import_feed_v2\|recipe_id_by_title\|insert_recipe\|--v2" --include=*.py --include=*.md --include=Makefile .
python -m saltbox import fixtures/feeds/larder_weekly_full.json   # on dev: expect added 0, updated 388, skipped 3 drafts
```

The grep should find nothing. Next is the separate commit for the search synonyms (aubergine/eggplant, rocket/arugula, swede/rutabaga).

## Verdict

PASS (core with the reasons-in-code rule) — importer under its plain name, history comments removed; one why-comment (match on the partner's id because titles get edited); no narration.
