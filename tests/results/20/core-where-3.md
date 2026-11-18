My tools are disconnected for the rest of this turn, so here is everything to apply. None of it has been run. The steps to check it are at the end.

**One thing first, before this reaches production:** once it's deployed, the nightly job picks up every JSON file in `incoming/`, about a week's backlog. If it runs on prod before the backfill (the one-off script that writes each partner's recipe id onto the recipes that came in by CSV), it will add the ~1,200 CSV-era recipes a second time. So ops has to run the backfill *and* someone has to clear its review CSVs on prod before the first nightly run there. Your 12 fixes were made in the dev admin, so prod will produce its own leftovers. Could you pass that on to ops along with the backfill step?

## What changes

- The JSON importer replaces the CSV one under the old names: `saltbox/importer.py`, function `import_feed`. The `_v2` names go away.
- The `--v2` flag goes away. `saltbox import <file>` now always reads JSON.
- The nightly job reads `*.json` instead of `*.csv`.
- The CSV code goes: the old importer, its tests, and the two `db.py` functions that only it used (`recipe_id_by_title`, `insert_recipe`).
- The backfill script stays until ops has run it on prod. After that it can be deleted (separate commit).

## 1. Move files

```sh
git rm saltbox/importer.py tests/test_importer.py
git mv saltbox/importer_v2.py saltbox/importer.py
git mv tests/test_importer_v2.py tests/test_importer.py
perl -pi -e 's/import_feed_v2/import_feed/g; s/importer_v2/importer/g' tests/test_importer.py
```

## 2. `saltbox/importer.py` (full file, replaces what the move left there)

Besides the renames, I rewrote the comments. The old ones recorded how the code got here ("v2:", "step 3:"), and now they give the reasons instead.

```python
"""Import a partner's JSON recipe feed."""
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
    """Import a partner's JSON feed.

    Safe to re-run on the same file: recipes already imported are updated, not added again.
    """
    result = ImportResult()
    feed = json.loads(path.read_text(encoding="utf-8"))
    for entry in feed["recipes"]:
        # Partners also send unpublished drafts; we only take published recipes.
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

## 3. `saltbox/cli.py` (full file)

```python
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

## 4. `saltbox/jobs/nightly.py`

I've only seen lines 8–16 of this file, so this is a diff:

```diff
-from saltbox.importer import import_feed
+from saltbox.importer import import_feed
@@
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

The import line keeps the same text because the function keeps its name. Files sort by name (`<partner>_<date>.json`), so each partner's backlog goes in date order and the newest version of a recipe is written last. The old CSV files left in `incoming/` are simply no longer matched by the glob.

## 5. `saltbox/db.py`

Delete `recipe_id_by_title` (starts at line 31) and `insert_recipe` (starts at line 38). My earlier search over `saltbox/` and `tests/` found no caller besides the old importer. That search didn't cover `scratch/`, and `scratch/match_titles.py` may use them. It's a throwaway script, but check before deleting if you want to keep it working.

## To check

- `pytest` (the whole suite, since the old importer's tests are gone and the renamed ones must still pass).
- `grep -rn "importer_v2\|import_feed_v2\|\-\-v2" .` should return nothing, including docs and READMEs I haven't seen.
- The nightly change has no test that I know of. To try it on dev, run `import_partner_feeds` once and confirm it reports the JSON files since 10 Nov with no new recipes beyond The Crumb's 2.

After this, the next commit is the search synonyms (aubergine/eggplant, rocket/arugula, swede/rutabaga), as agreed.


## Verdict

PASS (core with the "Where code goes" draft).

- Fails if: none apply. `importer_v2` becomes `importer.py` / `import_feed`; the `--v2` flag goes; the history comments are rewritten as reasons.
- Passes if: all hold. It says what was removed (the CSV importer, its test, `recipe_id_by_title`, `insert_recipe`), and the code is complete.
- Anti-goals: none tripped. The rule adds no new file and no split offer.
