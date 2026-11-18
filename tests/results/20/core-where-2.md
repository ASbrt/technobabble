My tools are disconnected for this turn, so here is the change for you to apply. None of it has been run: no tests, no import. Please run the checks at the end before you commit.

What it does: the JSON importer becomes the only importer, under the old name (`saltbox/importer.py`, `import_feed`). The `--v2` flag is gone. The nightly job reads the JSON files. The CSV code and its two database helpers are removed. The backfill script stays until ops have run it on prod.

## 1. Remove and rename files

```
git rm saltbox/importer.py tests/test_importer.py
git mv saltbox/importer_v2.py saltbox/importer.py
git mv tests/test_importer_v2.py tests/test_importer.py
sed -i '' 's/importer_v2/importer/g; s/import_feed_v2/import_feed/g' tests/test_importer.py
```

I haven't looked at the test file's import lines. The `sed` assumes it imports `import_feed_v2` from `saltbox.importer_v2`, so check the result. If `tests/test_importer.py` was the only user of some CSV fixtures, they can go too. I haven't checked which fixtures it used.

## 2. `saltbox/importer.py` (replace the whole file)

Same logic as v2. The docstring and comments no longer describe v1 or the order things were built in. Each one now gives the reason for the code.

```python
# saltbox/importer.py
"""Import a partner's recipe feed (JSON)."""
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
        # via str() so a JSON 0.1 becomes Decimal("0.1"), not the float's binary expansion
        "quantity": Decimal(str(item["qty"])) if item.get("qty") is not None else None,
        # partners spell units differently ("grams", "g")
        "unit": normalise_unit(item.get("unit")),
    }


def import_feed(conn, path: Path) -> ImportResult:
    """Import a partner's JSON feed. Re-importing the same feed updates, never duplicates."""
    result = ImportResult()
    feed = json.loads(path.read_text(encoding="utf-8"))
    for entry in feed["recipes"]:
        # partners send unpublished drafts in the same feed; we don't show them
        if entry.get("status") == "draft":
            result.skipped_drafts += 1
            continue
        # match on the partner's own id, not the title: partners edit titles
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

## 3. `saltbox/cli.py` (replace the whole file)

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

## 4. `saltbox/jobs/nightly.py`

Line 3 keeps its text, `from saltbox.importer import import_feed`, and now picks up the new module. Replace `import_partner_feeds`:

```python
def import_partner_feeds(conn):
    for path in sorted(INCOMING.glob("*.json")):
        if already_imported(conn, path):
            continue
        result = import_feed(conn, path)
        mark_imported(conn, path, result.added)
```

`mark_imported` used to get the count of added recipes, so I pass `result.added` to keep that meaning. I haven't read `mark_imported`. If its table should also record updates, that's a small follow-up.

The old CSV files in `incoming/` are already marked as imported, and the job no longer looks at `*.csv`, so they are simply ignored. On its first run, the job will catch up on every JSON file since 10 Nov. That's safe because re-importing is idempotent. Within a partner, `sorted` goes oldest to newest, because the date is in the filename.

## 5. `saltbox/db.py`

Delete `recipe_id_by_title` (starts at line 31) and `insert_recipe` (starts at line 38). My earlier grep across `saltbox/` and `tests/` found no callers other than the old importer and its test. That includes the backfill script, so it doesn't need them.

## 6. Local leftovers, not for the commit

- `backfill_review_larder_weekly.csv`, `backfill_review_the_crumb.csv`, `backfill_review_harvest_table.csv`: the dev review lists. You've dealt with them, so delete them.
- `scratch/match_titles.py`: my throwaway title-matching check. Delete it unless you want it.

## Kept on purpose: `saltbox/backfill_external_ids.py`

Ops need it on prod. Delete it in a later commit once they've run it. The order on prod matters, so it's worth passing on to ops:

1. `make migrate` (adds `partner` / `external_id` and the unique index, migration 0042).
2. Run the backfill for all three partners, as I did on dev.
3. Resolve the review CSVs it writes. The 12 recipes you fixed in the dev admin are fixed only on dev, so prod will most likely list the same ones again.
4. Only then let the nightly job run.

If the nightly job runs between the code deploy and step 2, it will add a second copy of almost every partner recipe (about 1,200). The simplest guard is to pause the nightly cron for the deploy.

## To check before committing

```
pytest -q
grep -rn "importer_v2\|import_feed_v2\|recipe_id_by_title\|insert_recipe\|--v2" saltbox tests
python -m saltbox import fixtures/feeds/larder_weekly_sample.json
```

The grep should print nothing. On dev, the import should print `added 0, updated 41, skipped 3 drafts`, because those recipes are already in.

After this comes the separate search commit: adding aubergine/eggplant, rocket/arugula and swede/rutabaga to `SYNONYMS` in `saltbox/search/config.py`.


## Verdict

PASS (core with the "Where code goes" draft).

- Fails if: none apply. `importer_v2` becomes `importer.py` / `import_feed`; the `--v2` flag goes; the history comments are rewritten as reasons.
- Passes if: all hold. It says what was removed (the CSV importer, its test, `recipe_id_by_title`, `insert_recipe`), and the code is complete.
- Anti-goals: none tripped. The rule adds no new file and no split offer.
