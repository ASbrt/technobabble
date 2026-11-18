My tools are disconnected, so here is the swap for you to apply. None of it has been run yet; the checks to run are at the end.

**Before the deploy: the order matters on prod.** Once this is deployed, the nightly job picks up the JSON files. If it runs before the backfill (the one-off script that gives existing CSV recipes their partner id), it duplicates every recipe that came in through CSV. The recipes the backfill can't match would duplicate too. On dev those were the 12 you fixed by hand in the admin, and prod will have its own list. So ops needs to run the backfill on prod **and** someone needs to work through its review CSVs before the first nightly run on the new code. It's worth telling them that when you brief them.

### What changes

- The JSON importer replaces the CSV one under the old name: `saltbox/importer.py`, `import_feed()`. `importer_v2.py` goes.
- `saltbox import` loses `--v2` and always reads JSON.
- The nightly job reads `*.json` instead of `*.csv`. The files that arrived since 10 Nov aren't marked as imported, so its first run imports all of them in date order per partner. That's safe because re-importing updates instead of adding.
- The CSV-only code goes: the old importer, its test, and `recipe_id_by_title()` / `insert_recipe()` in `db.py`. My earlier grep found no other caller in `saltbox/` or `tests/`.
- I also cut the comments that described the code by its history ("v2:", "step 3", "v1 stored…") and kept the reasons behind them.

**`saltbox/importer.py`** (replace the whole file)

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

    Safe to re-run on the same file: a recipe already imported is updated, not added again.
    """
    result = ImportResult()
    feed = json.loads(path.read_text(encoding="utf-8"))
    for entry in feed["recipes"]:
        # Partners include unpublished drafts in their feed; only published recipes go on the site.
        if entry.get("status") == "draft":
            result.skipped_drafts += 1
            continue
        # Match on the partner's own id, not the title: partners edit titles, and a title
        # match would turn every edited recipe into a duplicate.
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

**`saltbox/cli.py`** (replace the whole file)

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

**`saltbox/jobs/nightly.py`** (the import line stays as it is, because the name `import_feed` is unchanged)

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

I haven't seen `mark_imported()`. I'm assuming its third argument is the count of recipes added, because that's what it was given before. If it should also record updates or skipped drafts, it needs a change of its own.

**Files and functions to remove, and the v2 test to rename**

```sh
git rm saltbox/importer_v2.py tests/test_importer.py
git mv tests/test_importer_v2.py tests/test_importer.py
sed -i '' 's/saltbox\.importer_v2/saltbox.importer/; s/import_feed_v2/import_feed/g' tests/test_importer.py
```

In `saltbox/db.py`, delete `recipe_id_by_title()` (line 31) and `insert_recipe()` (line 38). I haven't seen their bodies, so I can't give you the exact diff.

### What stays, for now

- **`saltbox/backfill_external_ids.py`** stays until ops has run it on prod. It has no use after that, and I'll remove it in a follow-up once you tell me it's done. It's a one-off living inside the app package, so it shouldn't stay longer than that.
- **`scratch/match_titles.py`** (the title-matching script I used for the counts) wasn't in my grep, and it may import `recipe_id_by_title`. If `scratch/` is committed, delete the script along with the rest. If it isn't, it doesn't matter.
- **The old CSV files in `/srv/saltbox/incoming/`** are left alone. Nothing reads them any more.
- **CSV fixtures** in `fixtures/feeds/` may have been used only by the old test. I haven't checked, so look for `*.csv` there.

### To check after applying (none of this has been run)

```sh
pytest -q
python -m saltbox import fixtures/feeds/larder_weekly_sample.json   # on dev, expect: added 0, updated 41, skipped 3 drafts
grep -rn "importer_v2\|import_feed_v2\|\-\-v2\|recipe_id_by_title\|insert_recipe" --include=*.py .
```

The grep should only find `scratch/` if anything. Nothing tests the nightly job's change, so the first real check of it is a run on dev.

After this, I'll do the separate search-synonyms commit (aubergine/eggplant, rocket/arugula, swede/rutabaga).


## Verdict

PASS. (Core with the whole-tree version of "Where code goes".)

- Fails if: none apply. `importer_v2` becomes `importer.py` / `import_feed`; the `--v2` flag goes; the history comments are replaced by reasons.
- Passes if: all hold. It says what goes, and the code is complete.
- Anti-goals: none tripped. No restructure of the tree proposed. 839 words, against 581-835 earlier.
