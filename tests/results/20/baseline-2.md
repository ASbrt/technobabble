My tools are disconnected for this turn, so below are the changes for you to apply. I haven't run any of it. The pytest and grep at the end are the checks.

**Before deploying, one thing has to happen in the right order on prod.** As soon as the nightly job runs this code, it imports every JSON file since 10 Nov. If the backfill hasn't run on prod yet, all ~1,214 CSV-era recipes get duplicated. The prod deploy has to go:

1. `make migrate` (0042: the `partner` and `external_id` columns)
2. `python -m saltbox.backfill_external_ids --partner <p> <full export>` for each of `larder_weekly`, `the_crumb`, `harvest_table`. Ops need the partners' full exports on the prod box for this, not the daily files.
3. Review the three `backfill_review_*.csv` files **on prod**. You fixed the 12 leftovers in the dev admin, so prod still has them unfixed and will list them again.
4. Only then let the nightly job run.

If ops can't promise that order, tell me and I'll make the nightly job refuse to run while partner recipes without an `external_id` still exist.

## Commit 1: switch to the JSON importer and remove the CSV one

The v2 importer takes over the `importer.py` / `import_feed` names. The CSV code, its test and the `--v2` flag go away.

```sh
git rm saltbox/importer.py tests/test_importer.py
git mv saltbox/importer_v2.py saltbox/importer.py
git mv tests/test_importer_v2.py tests/test_importer.py
sed -i '' 's/saltbox\.importer_v2/saltbox.importer/g; s/import_feed_v2/import_feed/g' tests/test_importer.py
```

**`saltbox/importer.py`** (full file, replacing what the `git mv` brought over):

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
    result = ImportResult()
    feed = json.loads(path.read_text(encoding="utf-8"))
    for entry in feed["recipes"]:
        if entry.get("status") == "draft":
            result.skipped_drafts += 1
            continue
        # Match on the partner's id, not the title: titles get edited.
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

**`saltbox/cli.py`** (full file):

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

**`saltbox/jobs/nightly.py`**:

```diff
-from saltbox.importer import import_feed
+from saltbox.importer import import_feed
```
The import line itself stays the same, because `import_feed` now means the JSON importer. The function changes:

```python
def import_partner_feeds(conn):
    for path in sorted(INCOMING.glob("*.json")):
        if already_imported(conn, path):
            continue
        result = import_feed(conn, path)
        mark_imported(conn, path, result.added)
```

The old CSVs in `incoming/` are no longer matched. The file names are dated, so `sorted` imports the backlog oldest first and newer edits win. I've kept passing only the "added" count to `mark_imported` because I don't know if its table has room for updated or drafts. Say if you want those recorded too.

**`saltbox/db.py`**: delete `recipe_id_by_title` (line 31) and `insert_recipe` (line 38). The old importer was their only caller. My grep only covered `saltbox/` and `tests/`, so check for any other scripts that use them.

**Also delete** `scratch/match_titles.py` if it's tracked. It was a one-off for the title matching.

**Keep for now:** `saltbox/backfill_external_ids.py`. Prod needs it once (step 2 above). Delete it in a follow-up after the prod deploy.

Checks:

```sh
grep -rn "importer_v2\|import_feed_v2\|recipe_id_by_title\|insert_recipe\|--v2" --include=*.py saltbox tests   # expect nothing
pytest -q
```

## Commit 2 (separate, as agreed): search synonyms

**`saltbox/search/config.py`**:

```diff
 SYNONYMS = {
     "courgette": ["zucchini"],
     "coriander": ["cilantro"],
+    "aubergine": ["eggplant"],
+    "rocket": ["arugula"],
+    "swede": ["rutabaga"],
 }
```

These follow the existing entries' direction. I haven't checked the search code to see which way it applies the map. After applying, search "eggplant" on dev: you should get the 46 aubergine recipes.

## Verdict

PASS — importer_v2 renamed to importer, --v2 flag and old CSV importer removed, history comments removed; says what was deleted.
