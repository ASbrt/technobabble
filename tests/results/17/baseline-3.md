My tools are disconnected for this turn, so below is the code for you to apply. It's untested: run `pytest -q` and `python run_all.py --from 2` once it's in.

This adds a new stage 5 that reads stage 2's parquet and writes `site/data/readings_<year>.csv`. Because it's under `site/`, the files go live on the next `make publish`, and not before.

**New file `pipeline/s5_csv.py`:**

```python
"""Stage 5: the cleaned readings as one CSV per year, for download from the website.

Reads  data/clean/readings.parquet     (from stage 2)
Writes site/data/readings_<year>.csv   gauge_id, measured_at (UTC), level_m, flow_m3s
"""
from pathlib import Path

import pandas as pd


def run(data: Path) -> None:
    df = pd.read_parquet(data / "clean" / "readings.parquet")
    df = df.sort_values(["measured_at", "gauge_id"])
    out_dir = data.parent / "site" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    for year, part in df.groupby(df["measured_at"].dt.year):
        out = out_dir / f"readings_{year}.csv"
        part.to_csv(out, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
        print(f"  wrote {out}")
```

I haven't read `s4_charts.py`. If it finds `site/` some other way than `data.parent / "site"`, use the same method here so both stages write to the same folder.

**`run_all.py`, two changes:**

```diff
-from pipeline import s1_download, s2_clean, s3_daily, s4_charts
+from pipeline import s1_download, s2_clean, s3_daily, s4_charts, s5_csv
```

```diff
     (4, "charts for the website", s4_charts.run),
+    (5, "CSV downloads for the website", s5_csv.run),
 ]
```

**New test `tests/test_s5_csv.py`** (write it first and check it fails before adding the stage):

```python
import pandas as pd

from pipeline import s5_csv


def test_one_csv_per_year_with_faults_as_empty_cells(tmp_path):
    data = tmp_path / "data"
    (data / "clean").mkdir(parents=True)
    pd.DataFrame({
        "gauge_id": ["mill_pond"] * 3,
        "measured_at": pd.to_datetime(
            ["2025-12-31T23:45:00Z", "2026-01-01T00:00:00Z", "2026-01-01T00:15:00Z"], utc=True
        ),
        "level_m": [1.2, None, 1.3],
        "flow_m3s": [0.5, None, 0.6],
    }).to_parquet(data / "clean" / "readings.parquet", index=False)

    s5_csv.run(data)

    out = tmp_path / "site" / "data"
    assert sorted(p.name for p in out.iterdir()) == ["readings_2025.csv", "readings_2026.csv"]
    assert (out / "readings_2026.csv").read_text().splitlines() == [
        "gauge_id,measured_at,level_m,flow_m3s",
        "mill_pond,2026-01-01T00:00:00Z,,",
        "mill_pond,2026-01-01T00:15:00Z,1.3,0.6",
    ]
```

What the files will contain:

- **Times are in UTC with a `Z` suffix.** Splitting by UTC year gives the same result as splitting by UK year, because the UK is on GMT at New Year.
- **Readings the fault filter removed show up as empty cells.** The rows themselves stay in. The autumn hour from weir_lane that we dropped is simply missing.
- **The files should total a few MB.** That's about 140k rows, so roughly 2 MB a year.

Before you publish, check two things:

- **Licence.** hollins_bridge and mill_pond look like Environment Agency stations, and their data usually requires an attribution line. I don't know the terms for the council's weir_lane logger.
- **Links.** Stage 5 only writes the files. Nothing on the site links to them yet, and I haven't seen the page that would need the links.

Also, nothing is committed yet. If `scratch/neighbour_filter.py` is still there, delete it before you commit.

## Verdict

PASS — export added as stage 5 and wired into run_all.py, reading stage 2's output; no side script.
