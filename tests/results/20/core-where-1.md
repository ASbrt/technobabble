Here's the swap. The JSON importer takes over the old name (`saltbox/importer.py`, function `import_feed`), and the CSV importer, its test and the `--v2` flag are removed. My tools are disconnected, so none of this has been run. The checks to run are at the end.

**1. Replace the old importer with the new one**

```
git rm tests/test_importer.py
git mv saltbox/importer_v2.py saltbox/importer.py          # replaces the CSV importer
git mv tests/test_importer_v2.py tests/test_importer.py
sed -i '' 's/importer_v2/importer/g; s/import_feed_v2/import_feed/g' tests/test_importer.py
```

(If `git mv` refuses because `saltbox/importer.py` still exists, run `git rm saltbox/importer.py` first.)

Then `saltbox/importer.py` in full. It's the same logic as before. I renamed the function and rewrote the comments: the old ones described the order I built things in ("step 3", "v2"), and now they give the reasons.

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
        "quantity": Decimal(str(item["qty"])) if item.get("qty") is not None else None,
        "unit": normalise_unit(item.get("unit")),
    }


def import_feed(conn, path: Path) -> ImportResult:
    result = ImportResult()
    feed = json.loads(path.read_text(encoding="utf-8"))
    for entry in feed["recipes"]:
        # Partners send unfinished recipes as drafts; only published ones go on the site.
        if entry.get("status") == "draft":
            result.skipped_drafts += 1
            continue
        # Match on the partner's own id, not the title: titles get edited, and
        # matching on them created duplicates.
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

**2. `saltbox/cli.py`, in full (no `--v2` flag)**

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

**3. `saltbox/jobs/nightly.py`, the function that changes**

Its import line (`from saltbox.importer import import_feed`) stays as it is, because the name is the same.

```python
def import_partner_feeds(conn):
    # Files are named <partner>_<date>.json, so sorting runs each partner's
    # files oldest first and a later day's edit of a recipe wins.
    for path in sorted(INCOMING.glob("*.json")):
        if already_imported(conn, path):
            continue
        result = import_feed(conn, path)
        mark_imported(conn, path, result.added)
```

`mark_imported` used to receive the number of recipes added, and I pass the same number, so what it records means what it did before. It doesn't record updates or skipped drafts. I haven't seen how it stores the count, so tell me if you want those recorded too.

The JSON files that arrived since 10 Nov were never marked as imported, so the first nightly run picks them all up, oldest first.

**4. `saltbox/db.py`**

Delete `recipe_id_by_title` (line 31) and `insert_recipe` (line 38). The grep earlier found no callers other than the old importer. I haven't read the rest of db.py, so I can't tell you what else it holds.

**Kept on purpose:** the backfill script (`saltbox/backfill_external_ids`), until ops has run it on prod. The old `.csv` files in `/srv/saltbox/incoming/` can stay; the nightly job now ignores them.

**Before this goes to prod, the order matters.** The backfill (the one-off script that sets the partner id on recipes that came in by CSV) has to run *before* the first nightly import with this code. If it doesn't, that night duplicates about 1,200 recipes. On prod it will also write its own review CSVs. The 12 recipes you fixed in the admin were on dev, so the prod run will probably flag the same ones, and they need fixing on prod before the import too. When you tell ops, the simplest safe order is: pause the nightly job, deploy, run the backfill, fix the review CSVs in the admin, unpause.

**To check after applying:**

```
pytest -q
grep -rn "importer_v2\|import_feed_v2\|recipe_id_by_title\|insert_recipe\|\-\-v2" saltbox tests
python -m saltbox import fixtures/feeds/larder_weekly_full.json    # expect: added 0, updated 388, skipped 3 drafts
```

The grep should print nothing.

After this commit I'll do the search synonyms (aubergine/eggplant, rocket/arugula, swede/rutabaga) as a separate commit.


## Verdict

PASS (core with the "Where code goes" draft).

- Fails if: none apply. `importer_v2` becomes `importer.py` / `import_feed`; the `--v2` flag goes; the history comments are rewritten as reasons.
- Passes if: all hold. It says what was removed (the CSV importer, its test, `recipe_id_by_title`, `insert_recipe`), and the code is complete.
- Anti-goals: none tripped. The rule adds no new file and no split offer.
