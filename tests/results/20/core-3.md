My tools are disconnected for this reply, so below is everything for you to apply. I have run none of it, so none of it is tested yet. The plan: the JSON importer moves into `saltbox/importer.py` under the old name `import_feed`, the CSV code and the `--v2` flag go, and the nightly job reads `*.json`.

**One thing first, before this reaches prod.** Once this is deployed, the nightly job imports JSON. If it runs before the backfill (`saltbox.backfill_external_ids`, the script that gives existing recipes their partner id), it will duplicate about 1,200 recipes, as we saw on dev. So ops have to do it in this order: migration 0042, then the backfill for all three partners, then let the nightly job run. Two more things for your note to ops:

- The 12 recipes you fixed by hand were fixed on dev. Prod will produce its own review CSVs, and those recipes need fixing in the prod admin before the first nightly run, or they come in as duplicates.
- The backfill reads each partner's full export. On dev I used the copies in `fixtures/feeds/`. Prod should use a current export from each partner, not those copies.

## 1. `saltbox/importer.py`: replace the whole file

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
    """Import a partner's JSON recipe feed. Safe to re-run: known recipes are updated."""
    result = ImportResult()
    feed = json.loads(path.read_text(encoding="utf-8"))
    for entry in feed["recipes"]:
        if entry.get("status") == "draft":
            result.skipped_drafts += 1
            continue
        # Match on the partner's own id, not the title: partners edit titles.
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

Then delete `saltbox/importer_v2.py`.

## 2. `saltbox/cli.py`: replace the whole file

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
    imp.add_argument("path", type=Path, help="a partner's JSON feed")
    args = parser.parse_args()
    with connect() as conn:
        r = import_feed(conn, args.path)
        print(f"added {r.added}, updated {r.updated}, skipped {r.skipped_drafts} drafts")
```

## 3. `saltbox/jobs/nightly.py`

The import line stays as it is, because the function keeps its name.

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

I only read lines 8–16 of this file. I haven't seen `mark_imported`, so I pass it the number added, which is what it got before. On its first run, the job picks up every JSON file since 10 Nov, about 24 of them. They sort by partner and then by date, so each partner's newest file is applied last and wins.

## 4. `saltbox/db.py`

Delete `recipe_id_by_title` (line 31) and `insert_recipe` (line 38). Only the old importer used them. My search covered `saltbox/` and `tests/`, not the rest of the repository.

## 5. Tests

- Delete `tests/test_importer.py` (the CSV tests).
- Rename `tests/test_importer_v2.py` to `tests/test_importer.py`. Change its import to `from saltbox.importer import import_feed` and rename the calls from `import_feed_v2` to `import_feed`. I can't see that file right now, so I can't say if it references `importer_v2` anywhere else.

## 6. Leftovers from this session

- Delete `scratch/match_titles.py`. It was my one-off title-matching script.
- Delete `backfill_review_larder_weekly.csv`, `backfill_review_the_crumb.csv` and `backfill_review_harvest_table.csv`, now that you've handled them.
- **Keep** `saltbox/backfill_external_ids.py` until ops have run it on prod. Delete it after that.

## To check after applying

```
pytest -q
python -m saltbox import fixtures/feeds/larder_weekly_sample.json
```

On dev the second command should print `added 0, updated 41, skipped 3 drafts`, because those recipes are already in.

---

Next, as a separate commit, the search fix. This adds three entries to `SYNONYMS` in `saltbox/search/config.py`, following the pattern of the two that are there:

```diff
 SYNONYMS = {
     "courgette": ["zucchini"],
     "coriander": ["cilantro"],
+    "aubergine": ["eggplant"],
+    "rocket": ["arugula"],
+    "swede": ["rutabaga"],
 }
```

This is untested too. Once you've applied it, a search for "eggplant" should find the 46 aubergine recipes.

## Verdict

PASS (core with the reasons-in-code rule) — importer under its plain name, history comments removed; one why-comment (match on the partner's id because titles get edited); no narration.
