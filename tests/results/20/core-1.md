Here's the swap. The JSON importer moves into `importer.py` and takes over the name `import_feed`. The CSV importer, the `--v2` flag and the two database helpers only the CSV path used are removed, and the nightly job picks up `*.json`. I couldn't run anything after writing this, so it is all untested. Run the checks at the end before you merge.

**1. `saltbox/importer.py`: replace the whole file**

```python
# saltbox/importer.py
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
        # via str() so a JSON float like 1.1 doesn't become 1.100000000000000088...
        "quantity": Decimal(str(item["qty"])) if item.get("qty") is not None else None,
        "unit": normalise_unit(item.get("unit")),
    }


def import_feed(conn, path: Path) -> ImportResult:
    """Import a partner's JSON feed. Re-importing a feed updates recipes, never duplicates them."""
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

**2. Delete `saltbox/importer_v2.py`**

**3. `saltbox/cli.py`: replace the whole file**

```python
# saltbox/cli.py
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

**4. `saltbox/jobs/nightly.py`**

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

The import on line 3 doesn't change, because the function keeps its name. I've only seen `mark_imported` called, never its definition, so I've kept passing it the count of added recipes as before. If it should also record updates, that's a change to `mark_imported` itself.

**5. `saltbox/db.py`:** delete `recipe_id_by_title` (line 31) and `insert_recipe` (line 38). My earlier grep found no other callers in `saltbox/` or `tests/`.

**6. Tests**

```
git rm tests/test_importer.py
git mv tests/test_importer_v2.py tests/test_importer.py
```

In the moved file, change `from saltbox.importer_v2 import import_feed_v2` to `from saltbox.importer import import_feed`, and rename the calls to match.

**Not removed: `saltbox/backfill_external_ids.py`.** It stays until it has run on prod. After that it can go in a follow-up commit.

**Order for ops.** This matters, because the first nightly run on prod will import every JSON file since 10 Nov (none are marked as imported yet):

1. Apply migration `0042_recipes_external_id.sql`. It adds the partner and external id columns.
2. Run the backfill for all three partners.
3. Check the review CSVs it writes. Your fixes from today were made in the dev admin, so prod will flag the same ~12 recipes again. Any recipe left unmatched gets a duplicate on the next import.
4. Only then deploy this change. If the nightly job runs on the new code before steps 2 and 3, it will duplicate roughly 1,200 recipes.

The simplest way to make that safe is to hold this deploy until the backfill is done and the review CSVs are cleared.

**Checks to run:** `pytest`, then `grep -rn "importer_v2\|import_feed_v2\|recipe_id_by_title\|insert_recipe" saltbox tests` (should print nothing), then one run of `import_partner_feeds` against dev. Every file has already been imported there, so it should report only updates.

The search synonyms (aubergine/eggplant, rocket/arugula, swede/rutabaga) come next, as their own commit.

## Verdict

PASS (core with the reasons-in-code rule) — importer under its plain name, history comments removed; one why-comment (match on the partner's id because titles get edited); no narration.
