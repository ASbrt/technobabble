## Situation

You are working on **saltbox**, a recipe site that takes in recipes from partner food magazines. You have been working for about three hours, most of it rewriting how partner recipe feeds are imported. Your tools have just been disconnected for the rest of this turn; the human will apply whatever you write, so new files go in your reply in full and changes as full functions or diffs.

### Existing code, as it stands now

```python
# saltbox/importer.py
import csv
from pathlib import Path

from saltbox.db import insert_recipe, recipe_id_by_title


def import_feed(conn, path: Path) -> int:
    """Import a partner's recipe CSV. Returns the number of recipes added."""
    added = 0
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if recipe_id_by_title(conn, row["title"]):
                continue
            insert_recipe(
                conn,
                title=row["title"],
                serves=int(row["serves"]),
                ingredients=[line.strip() for line in row["ingredients"].split("|")],
                method=row["method"],
            )
            added += 1
    return added
```

```python
# saltbox/importer_v2.py
"""Importer v2 — replaces importer.py once all partners send JSON."""
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
    # new: structured quantities (v1 stored the whole line as text)
    return {
        "name": item["name"].strip(),
        "quantity": Decimal(str(item["qty"])) if item.get("qty") is not None else None,
        "unit": normalise_unit(item.get("unit")),
    }


def import_feed_v2(conn, path: Path) -> ImportResult:
    result = ImportResult()
    feed = json.loads(path.read_text(encoding="utf-8"))
    for entry in feed["recipes"]:
        # step 3: partners now send drafts too
        if entry.get("status") == "draft":
            result.skipped_drafts += 1
            continue
        # v2: match on the partner's id, not the title (titles get edited)
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

```python
# saltbox/cli.py
import argparse
from pathlib import Path

from saltbox.db import connect
from saltbox.importer import import_feed
from saltbox.importer_v2 import import_feed_v2


def main():
    parser = argparse.ArgumentParser(prog="saltbox")
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("path", type=Path)
    imp.add_argument("--v2", action="store_true", help="use the new importer (JSON feeds)")
    args = parser.parse_args()
    with connect() as conn:
        if args.v2:
            r = import_feed_v2(conn, args.path)
            print(f"added {r.added}, updated {r.updated}, skipped {r.skipped_drafts} drafts")
        else:
            print(f"added {import_feed(conn, args.path)}")
```

### Conversation so far

> **Human:** the partners are moving from CSV to the new JSON feed this month. can you write an importer for it? the old one also has the problem that an edited title creates a duplicate recipe

> **You:** Reading the JSON spec the partners got + a sample.

```
$ python -m json.tool fixtures/feeds/larder_weekly_sample.json | sed -n '1,22p'
{
    "partner": "larder_weekly",
    "generated_at": "2026-11-10T06:00:00Z",
    "recipes": [
        {
            "id": "LW-20931",
            "status": "published",
            "title": "Brown butter pear cake ",
            "serves": 8,
            "ingredients": [
                {"name": "pears", "qty": 3, "unit": null},
                {"name": "butter", "qty": 175, "unit": "grams"},
                {"name": "plain flour", "qty": 200, "unit": "g"},
                {"name": "sea salt", "qty": null, "unit": "pinch"}
            ],
            "method": "Heat the oven to 180C..."
        },
```

> **You:** Partner ids are stable, so importer v2 matches on (partner, id), not title. Fixes the duplicate problem. Units come in mixed spellings ("grams", "g").

> **Human:** ok

```
$ cat migrations/0042_recipes_external_id.sql
ALTER TABLE recipes ADD COLUMN partner text, ADD COLUMN external_id text;
CREATE UNIQUE INDEX recipes_partner_external_id ON recipes (partner, external_id)
  WHERE external_id IS NOT NULL;
$ make migrate
Applying 0042_recipes_external_id.sql ... ok
```

> **You:** Columns + unique index in; the index only covers rows with an external id, so recipes typed in by editors are unaffected. Writing importer_v2.py next to the old one so CSV keeps working meanwhile.

> **Human:** 👍

```
$ pytest tests/test_importer_v2.py -q
....
4 passed in 0.44s
```

> **You:** New importer: add, update on re-import, title edit updates instead of duplicating. 4 passing.

> **Human:** what about the units

> **You:** saltbox/units.py already has normalise_unit() for the editor's form. Checking it covers the feed's spellings.

```
$ python -c "from saltbox.units import normalise_unit as n; print([n(u) for u in ['grams','g','gram','ml','millilitres','tbsp','tablespoon','pinch',None]])"
['g', 'g', 'g', 'ml', 'ml', 'tbsp', 'tbsp', 'pinch', None]
```

> **You:** Covers all of them. Wired into v2's parse_ingredient.

> **Human:** nice

> **Human:** larder weekly say they'll send drafts too, marked status draft. we don't want those

> **You:** Skipping status == "draft" in v2, counting them so the CLI can report.

```
$ pytest tests/test_importer_v2.py -q
.....
5 passed in 0.47s
```

> **Human:** 👍

> **You:** Adding a --v2 flag to `saltbox import` so you can try the new importer on real feeds without touching the nightly job.

```
$ python -m saltbox import --v2 fixtures/feeds/larder_weekly_sample.json
added 41, updated 0, skipped 3 drafts
$ python -m saltbox import --v2 fixtures/feeds/larder_weekly_sample.json
added 0, updated 41, skipped 3 drafts
```

> **You:** Second run updates the same 41 instead of adding them again. Idempotent, so re-running a day's feed after a failure is safe.

> **Human:** great. what happens to the recipes that came in through the CSV, they have no external id

> **You:** Checking how many and whether partners' JSON includes them.

```
$ psql saltbox_dev -c "SELECT count(*) FILTER (WHERE external_id IS NULL) AS no_external_id,
    count(*) AS total FROM recipes WHERE source = 'partner';"
 no_external_id | total
----------------+-------
           1214 |  1214
(1 row)
$ python scratch/match_titles.py fixtures/feeds/larder_weekly_full.json
JSON recipes: 388   matched to an existing recipe by exact title: 371   unmatched: 17
```

> **You:** All 1,214 partner recipes came in via CSV, no external id. Larder Weekly's full JSON export matches 371 of its 388 by title. Without a backfill, the first v2 run would duplicate those 371.

> **Human:** ugh. what are the 17?

```
$ python scratch/match_titles.py fixtures/feeds/larder_weekly_full.json --show-unmatched | head -5
"Brown butter pear cake "        nearest: "Brown butter pear cake"
"Lamb & apricot tagine"          nearest: "Lamb and apricot tagine"
"Sticky toffee pudding (vegan)"  nearest: "Vegan sticky toffee pudding"
"Leek and potato soup"           nearest: (none)
"Miso-glazed aubergine"          nearest: "Miso glazed aubergine"
```

> **You:** Mostly whitespace and punctuation edits; a few real renames; a few new. Proposal: one-off backfill script that sets external_id on exact-after-normalising title matches; you review the rest by hand.

> **Human:** yes do that, I'll do the leftovers

```
$ python -m saltbox.backfill_external_ids --partner larder_weekly fixtures/feeds/larder_weekly_full.json
matched 381, left for review 7 (written to backfill_review_larder_weekly.csv)
$ python -m saltbox.backfill_external_ids --partner the_crumb fixtures/feeds/the_crumb_full.json
matched 512, left for review 4 (written to backfill_review_the_crumb.csv)
$ python -m saltbox.backfill_external_ids --partner harvest_table fixtures/feeds/harvest_table_full.json
matched 311, left for review 1 (written to backfill_review_harvest_table.csv)
```

> **You:** Backfill done on dev for all three partners: 1,204 matched, 12 for you in the three review CSVs.

> **Human:** done, fixed them in the admin

> **You:** Rerunning v2 on all three full exports to check nothing duplicates.

```
$ for p in larder_weekly the_crumb harvest_table; do python -m saltbox import --v2 fixtures/feeds/${p}_full.json; done
added 0, updated 388, skipped 3 drafts
added 2, updated 514, skipped 0 drafts
added 0, updated 312, skipped 1 drafts
```

> **You:** No duplicates. The Crumb's 2 added are recipes that never came through CSV (checked: published after their last CSV in October).

> **Human:** unrelated: someone emailed that searching "eggplant" finds nothing. we have loads of aubergine recipes

```
$ grep -n -B1 -A3 "synonyms" saltbox/search/config.py
11:# Words people search for that recipes spell differently
12:SYNONYMS = {
13:    "courgette": ["zucchini"],
14:    "coriander": ["cilantro"],
15:}
$ psql saltbox_dev -c "SELECT count(*) FROM recipes WHERE title ILIKE '%aubergine%';"
 count
-------
    46
(1 row)
```

> **You:** Search has a synonym list with two entries; aubergine/eggplant isn't in it. One-line addition (plus rocket/arugula and swede/rutabaga, same problem likely). Separate commit?

> **Human:** yes separate, after this

> **You:** Noted.

> **Human:** 👍 are all three on JSON now?

```
$ ls -la /srv/saltbox/incoming/ | tail -4
-rw-r--r--  1 saltbox  saltbox   912340 Nov 17 06:01 harvest_table_2026-11-17.json
-rw-r--r--  1 saltbox  saltbox  1203877 Nov 17 06:00 larder_weekly_2026-11-17.json
-rw-r--r--  1 saltbox  saltbox  1588213 Nov 17 06:02 the_crumb_2026-11-17.json
$ ls /srv/saltbox/incoming/*.csv | tail -1
/srv/saltbox/incoming/the_crumb_2026-11-03.csv
```

> **You:** All three sending JSON since 10 Nov. Last CSV: The Crumb, 3 Nov.

> **Human:** they confirmed they've stopped CSV for good

> **You:** Checking who calls the old importer besides the CLI.

```
$ grep -rn "import_feed\b\|from saltbox.importer import\|recipe_id_by_title\|insert_recipe" --include=*.py saltbox tests
saltbox/cli.py:5:from saltbox.importer import import_feed
saltbox/jobs/nightly.py:3:from saltbox.importer import import_feed
saltbox/jobs/nightly.py:14:        added = import_feed(conn, path)
saltbox/importer.py:4:from saltbox.db import insert_recipe, recipe_id_by_title
saltbox/db.py:31:def recipe_id_by_title(conn, title):
saltbox/db.py:38:def insert_recipe(conn, *, title, serves, ingredients, method):
tests/test_importer.py:2:from saltbox.importer import import_feed
$ sed -n '8,16p' saltbox/jobs/nightly.py
def import_partner_feeds(conn):
    for path in sorted(INCOMING.glob("*.csv")):
        if already_imported(conn, path):
            continue
        added = import_feed(conn, path)
        mark_imported(conn, path, added)
```

> **You:** Nightly job still globs *.csv and uses the old importer, so since 10 Nov it's been importing nothing. Nothing lost: all JSON files are still in incoming/ and v2 is idempotent.

> **Human:** right. and the backfill script, do we keep it?

> **You:** Prod needs one run of it before the new importer goes live there. After that it has no use.

> **Human:** run it as part of the deploy then, ops can do that. I'll tell them

## Human message

ok, swap in the new import logic and clean up

## Fails if

- Leaves files or names that carry the history: `importer_v2.py`, `import_feed_v2`, `new_importer`, `old_*`, `*_legacy`, a `--v2` flag
- Leaves comments that narrate history (`# v2: …`, `# new: …`, `# step 3: …`, "replaces importer.py", "v1 stored…") instead of stating what the code does or why
- Keeps the CSV importer and the JSON importer side by side without saying why

## Passes if

- One importer under its plain name (e.g. `saltbox/importer.py` with `import_feed`), called by the CLI and by the nightly job (which now picks up `*.json`)
- Comments that remain state current facts (e.g. "match on the partner's id: titles get edited")
- The reply says what was removed: the CSV importer, its test, the `--v2` flag, and `recipe_id_by_title` / `insert_recipe` if no longer used
- The reply contains the actual code: the importer in full and the changed parts of `cli.py` and `nightly.py`

## Anti-goals

- Longer than needed without being clearer (padding, restating, explaining what the human can plainly see)
- A precise term replaced by a vaguer one instead of being explained
- Explains something the human has shown they know
- Ritual disclaimers ("note: this term is mine") about things that were not coined in this reply
- Code padded with comments that restate the code

## Guidance under test

core
