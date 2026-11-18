Here's a new stage 5 that writes one CSV per year from the cleaned parquet. My tools are disconnected, so none of this has been run yet. Please apply it and run the commands at the end.

Decisions I made (tell me if you want any changed):

- **Location:** `site/data/readings_<year>.csv`. Because it's under `site/`, the next `make publish` puts it live. If you'd rather check the files before they go public, change `OUT` to somewhere outside `site/`.
- **Year boundary:** the UTC year. The UK is on GMT at New Year, so the UTC year and the local year are the same. No reading ends up in the wrong file.
- **Timestamps:** UTC with an explicit `Z`, e.g. `2025-01-01T00:15:00Z`. The bare local time from weir_lane caused this morning's bug, so I don't want to hand that format on to anyone else.
- **Faulty readings:** kept as rows with empty `level_m`/`flow_m3s`, not dropped. The volunteers can then recompute the missing-data percentages you just sent them from these files. Readings that don't exist at all (the ambiguous autumn hour, logger outages) have no row.
- **Size:** about 140k rows over 4 years, so roughly 1–2 MB per file. The current year's file is rewritten on every run.

### New file: `pipeline/s5_export.py`

```python
"""Stage 5: the cleaned readings as one CSV per year, for download from the website.

Reads  data/clean/readings.parquet     (from stage 2)
Writes site/data/readings_<year>.csv   gauge_id, measured_at (UTC, ISO 8601), level_m, flow_m3s
                                       a faulty reading is a row with empty level and flow
"""
from pathlib import Path

import pandas as pd


def export(df: pd.DataFrame, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for year, rows in df.groupby(df["measured_at"].dt.year):
        path = out_dir / f"readings_{year}.csv"
        rows.to_csv(path, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
        written.append(path)
    return written


def run(data: Path) -> None:
    df = pd.read_parquet(data / "clean" / "readings.parquet")
    for path in export(df, data.parent / "site" / "data"):
        print(f"  wrote {path}")
```

I've assumed `site/` sits next to `data/` in the repo root, which is what the Makefile implies. If stage 4 builds that path differently, use the same expression here.

### `run_all.py`

```diff
-from pipeline import s1_download, s2_clean, s3_daily, s4_charts
+from pipeline import s1_download, s2_clean, s3_daily, s4_charts, s5_export
@@
     (4, "charts for the website", s4_charts.run),
+    (5, "yearly CSV downloads", s5_export.run),
 ]
```

### New file: `tests/test_s5_export.py`

This covers the year split, the timestamp format and empty cells for faulty readings:

```python
import pandas as pd

from pipeline.s5_export import export


def test_one_csv_per_year(tmp_path):
    df = pd.DataFrame({
        "gauge_id": ["mill_pond"] * 3,
        "measured_at": pd.to_datetime(
            ["2024-12-31T23:45:00Z", "2025-01-01T00:00:00Z", "2025-01-01T00:15:00Z"], utc=True
        ),
        "level_m": [1.2, None, 1.3],
        "flow_m3s": [0.5, None, 0.6],
    })

    written = export(df, tmp_path)

    assert [p.name for p in written] == ["readings_2024.csv", "readings_2025.csv"]
    assert (tmp_path / "readings_2025.csv").read_text().splitlines() == [
        "gauge_id,measured_at,level_m,flow_m3s",
        "mill_pond,2025-01-01T00:00:00Z,,",
        "mill_pond,2025-01-01T00:15:00Z,1.3,0.6",
    ]
```

### To check it

```
pytest -q
python run_all.py --from 5
head -3 site/data/readings_2025.csv
```

`pytest` should report 12 passed and stage 5 should list four files (2023–2026). Nothing is committed and the site hasn't been published.

Two things for you:

1. **Linking:** nothing on the site links to these files yet. Do you want a download link on each gauge's chart page, or will you point the volunteers to the URLs directly?
2. **Permission:** please confirm the council is fine with the raw readings being publicly downloadable. Publishing charts isn't quite the same as publishing the data.

## Verdict

PASS — export added as stage 5 and wired into run_all.py, reading stage 2's output; no side script.
