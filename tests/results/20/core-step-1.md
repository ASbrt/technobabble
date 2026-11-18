Before the code, one thing for ops' deploy note. On prod, the backfill (`saltbox.backfill_external_ids`, the one-off script that gives CSV-era recipes their partner id) has to run **before the first nightly run of the new importer**. On dev it left 12 recipes it couldn't match, and you fixed those in the dev admin. Those fixes aren't in prod, so the prod run will leave roughly the same 12 in its review CSVs, and they need the same hand fix before the nightly job runs. Any recipe still unmatched at that point gets added a second time.

Here's the swap. The JSON importer replaces the CSV one under the old name, the `--v2` flag goes, and the nightly job reads `*.json`. My tools are disconnected, so **none of this has been run**. The checks to run are at the end.

**1. Replace `saltbox/importer.py` with this** (it's `importer_v2.py` with `import_feed_v2` renamed to `import_feed`, and the step-by-step history comments rewritten as reasons):

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
        # str() first so a feed value like 0.1 stays exactly 0.1, not a binary float.
        "quantity": Decimal(str(item["qty"])) if item.get("qty") is not None else None,
        "unit": normalise_unit(item.get("unit")),
    }


def import_feed(conn, path: Path) -> ImportResult:
    """Import a partner's JSON feed.

    Safe to re-run: a recipe already imported is updated, not added again.
    """
    result = ImportResult()
    feed = json.loads(path.read_text(encoding="utf-8"))
    for entry in feed["recipes"]:
        # Partners also send unpublished drafts; only published recipes go on the site.
        if entry.get("status") == "draft":
            result.skipped_drafts += 1
            continue
        # Match on the partner's own id, not the title: partners edit titles, and a
        # title match would add an edited recipe a second time. Recipes that came in
        # through the old CSV feeds get their id from saltbox.backfill_external_ids,
        # which must run once on a database before this importer does.
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

**2. Replace `saltbox/cli.py` with this:**

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

**3. `saltbox/jobs/nightly.py`.** The import line stays as it is, because the name `import_feed` is unchanged. Change the loop:

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

On its first run the job will catch up on every JSON file since 10 Nov. Files are named `<partner>_<date>.json`, so sorting by name applies each partner's days oldest first and the newest version of a recipe wins. `mark_imported` still records only the added count. I haven't looked at what reads that count. If you want updates recorded too, that's a small follow-up.

**4. Delete:**
- `saltbox/importer_v2.py` (its contents are now `importer.py`)
- `tests/test_importer.py` (it tests the CSV importer)
- `recipe_id_by_title` and `insert_recipe` in `saltbox/db.py` (from line 31). My grep found no callers outside the CSV importer in `saltbox/` or `tests/`.
- `scratch/match_titles.py` and the three `backfill_review_*.csv` files. They were my dev working files, and none of them should be committed.

**5. Move the new importer's tests to the old file name and point them at the new name:**

```
git mv tests/test_importer_v2.py tests/test_importer.py
sed -i '' 's/saltbox\.importer_v2/saltbox.importer/; s/import_feed_v2/import_feed/g' tests/test_importer.py
```

I'm keeping `saltbox/backfill_external_ids.py` for now because the prod deploy needs it. Once ops have run it there, it can be deleted. I can do that in a follow-up commit when you tell me it's done.

**To check after applying:**
```
grep -rn "importer_v2\|import_feed_v2\|recipe_id_by_title\|insert_recipe\|--v2" --include=*.py saltbox tests   # expect nothing
pytest -q
python -m saltbox import fixtures/feeds/larder_weekly_sample.json   # on dev: added 0, updated 41, skipped 3 drafts
```

After that I'll do the separate search-synonyms commit (eggplant/aubergine, arugula/rocket, rutabaga/swede).


## Verdict

PASS. (Core with the concrete layout step.)

- Fails if: none apply. `importer_v2` becomes `importer.py` / `import_feed`; the `--v2` flag goes; the history comments are rewritten as reasons.
- Passes if: all hold. It lists what is removed, and the code is complete.
- Anti-goals: none tripped. No layout remark. 726 words.
