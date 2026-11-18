My tools are disconnected, so this is the full change for you to apply. I haven't run any of it.

**What changes**

- The nightly job reads `*.json` and uses the new importer. It skips files it has already imported, and none of the JSON files since 10 Nov were imported, so the first run picks up everything from 10 Nov on.
- The CSV importer, its tests, the `--v2` flag and the two db helpers that only the old importer used are deleted.
- `importer_v2.py` becomes `importer.py` and `import_feed_v2` becomes `import_feed`. With CSV gone, "v2" no longer means anything. I also removed the comments that told the story of the session ("new:", "step 3:", "v2:").
- I kept the backfill script, because prod still needs its one run.

**1. Files**

```sh
git rm saltbox/importer.py tests/test_importer.py
git mv saltbox/importer_v2.py saltbox/importer.py
git mv tests/test_importer_v2.py tests/test_importer.py
```

In `tests/test_importer.py`, change `from saltbox.importer_v2 import import_feed_v2` to `from saltbox.importer import import_feed`, and change every call from `import_feed_v2(` to `import_feed(`.

**2. `saltbox/importer.py` (replace the whole file)**

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

    Recipes are matched on (partner, id), not title, so an edited title updates
    the recipe instead of duplicating it, and re-importing a feed is safe.
    Drafts are skipped.
    """
    result = ImportResult()
    feed = json.loads(path.read_text(encoding="utf-8"))
    for entry in feed["recipes"]:
        if entry.get("status") == "draft":
            result.skipped_drafts += 1
            continue
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

**3. `saltbox/cli.py` (replace the whole file)**

```python
import argparse
from pathlib import Path

from saltbox.db import connect
from saltbox.importer import import_feed


def main():
    parser = argparse.ArgumentParser(prog="saltbox")
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("path", type=Path, help="a partner's JSON feed")
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

The import line (`from saltbox.importer import import_feed`) stays the same. I've only seen `mark_imported` called with an int, so I pass `result.added`. If it can store more, `updated` is worth recording too.

**5. `saltbox/db.py`**

Delete `recipe_id_by_title` (line 31) and `insert_recipe` (line 38). My grep found no callers in `saltbox/` or `tests/` other than the old importer.

**6. Check**

```sh
pytest -q
grep -rn "importer_v2\|import_feed_v2\|\-\-v2\|insert_recipe\|recipe_id_by_title" .
```

The grep should come back empty. It also covers READMEs and runbooks that might mention `--v2`.

**Deploy order, for ops**

On prod, the backfill has to run **before** the first nightly run with this code. If the job runs first, it adds the ~1,200 CSV-era recipes a second time. Ops need each partner's full JSON export on prod for that run, and the 12 leftovers you fixed by hand on dev will turn up again in prod's review CSVs. Once the backfill has run on prod, the script can be deleted in a follow-up.

After this, I'll do the search synonyms (aubergine/eggplant, rocket/arugula, swede/rutabaga) as a separate commit.

## Verdict

PASS — importer_v2 renamed to importer, --v2 flag and old CSV importer removed, history comments removed; says what was deleted.
