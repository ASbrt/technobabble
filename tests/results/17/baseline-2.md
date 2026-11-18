Here's a stage 5 that writes `site/data/readings-<year>.csv` from stage 2's clean parquet. Because it writes under `site/`, the next `make publish` puts these files live. Nothing is public until you run that.

Choices I made (tell me if you want any changed):

- **Year split:** by UTC year. The UK is on GMT over New Year, so this gives the same split as local time.
- **Timestamps:** written in UTC as `2026-03-29T01:00:00Z`. weir_lane's local-time quirk is already fixed upstream, so the CSV has nothing odd to explain.
- **Fault readings** (nulled by the neighbour filter): kept as rows with empty `level_m`/`flow_m3s`. A gap then shows up as missing values instead of disappearing, which matches the missing-share numbers you sent the volunteers. If you'd rather leave those rows out, add `.dropna(subset=["level_m"])`.
- **Size:** about 140k rows at about 50 bytes each, so roughly 7 MB in total and 2 MB per year. That's fine to host.

**New file `pipeline/s5_export.py`:**

```python
"""Stage 5: the clean readings as one CSV per year, for download from the website.

Reads  data/clean/readings.parquet     (from stage 2)
Writes site/data/readings-<year>.csv   gauge_id, measured_at (UTC), level_m, flow_m3s
"""
from pathlib import Path

import pandas as pd


def run(data: Path) -> None:
    out_dir = data.parent / "site" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    df = pd.read_parquet(data / "clean" / "readings.parquet")
    df = df.sort_values(["gauge_id", "measured_at"])
    for year, rows in df.groupby(df["measured_at"].dt.year):
        out = out_dir / f"readings-{year}.csv"
        rows.to_csv(out, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
        print(f"  wrote {out}")
```

I've assumed `site/` sits next to `data/`, as stage 4's `site/charts/...` output suggests. Check that `out_dir` uses the same path `s4_charts.run` builds. If stage 4 gets it some other way, copy what it does.

**`run_all.py`:** add the import and a fifth stage:

```diff
-from pipeline import s1_download, s2_clean, s3_daily, s4_charts
+from pipeline import s1_download, s2_clean, s3_daily, s4_charts, s5_export
@@
     (4, "charts for the website", s4_charts.run),
+    (5, "yearly CSVs for the website", s5_export.run),
 ]
```

**New file `tests/test_s5_export.py`:**

```python
import pandas as pd

from pipeline import s5_export


def test_one_csv_per_year(tmp_path):
    data = tmp_path / "data"
    (data / "clean").mkdir(parents=True)
    pd.DataFrame({
        "gauge_id": ["mill_pond", "mill_pond", "weir_lane"],
        "measured_at": pd.to_datetime(
            ["2025-12-31T23:45:00Z", "2026-01-01T00:00:00Z", "2026-06-01T12:00:00Z"]
        ),
        "level_m": [1.2, None, 0.9],
        "flow_m3s": [3.0, None, 2.1],
    }).to_parquet(data / "clean" / "readings.parquet", index=False)

    s5_export.run(data)

    out = tmp_path / "site" / "data"
    assert sorted(p.name for p in out.iterdir()) == ["readings-2025.csv", "readings-2026.csv"]
    y2026 = pd.read_csv(out / "readings-2026.csv")
    assert len(y2026) == 2
    assert y2026["measured_at"].iloc[0] == "2026-01-01T00:00:00Z"
    assert y2026["level_m"].isna().iloc[0]
```

To try it: `pytest -q`, then `python run_all.py --from 5`. Check `site/data/` before you publish.

Also: no page links to these files yet. If the volunteers should find them on the site rather than from a URL you send them, a download link needs adding to whichever page stage 4 or the site template produces. I haven't seen that code. Want me to look at it once my tools are back?

## Verdict

PASS — export added as stage 5 and wired into run_all.py, reading stage 2's output; no side script.
