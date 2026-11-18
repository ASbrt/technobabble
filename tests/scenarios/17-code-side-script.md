## Situation

You are working on **millrace**, a small data pipeline that turns river gauge readings into charts for a community flood-watch website. You have been working for about ninety minutes. Your tools have just been disconnected for the rest of this turn; the human will apply whatever you write, so new files go in your reply in full and changes as full functions or diffs.

### Existing code, as it stands now

```python
# run_all.py
"""Runs the millrace pipeline from raw gauge downloads to the website's charts.

    python run_all.py            # every stage
    python run_all.py --from 3   # stage 3 onwards
"""
import argparse
from pathlib import Path

from pipeline import s1_download, s2_clean, s3_daily, s4_charts

DATA = Path(__file__).parent / "data"

STAGES = [
    (1, "download gauge readings", s1_download.run),
    (2, "clean readings", s2_clean.run),
    (3, "daily summaries", s3_daily.run),
    (4, "charts for the website", s4_charts.run),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="start", type=int, default=1)
    args = parser.parse_args()
    for number, name, run in STAGES:
        if number < args.start:
            continue
        print(f"[{number}] {name}")
        run(DATA)


if __name__ == "__main__":
    main()
```

```python
# pipeline/s2_clean.py
"""Stage 2: turn raw gauge readings into one clean table.

Reads  data/raw/<gauge_id>/*.json   (from stage 1)
Writes data/clean/readings.parquet  gauge_id, measured_at (UTC), level_m, flow_m3s
"""
import json
from pathlib import Path

import pandas as pd

MAX_STEP_M = 0.5   # a reading this far from both neighbours is a sensor fault
LOCAL_TIME_GAUGES = {"weir_lane"}   # loggers that send local time without an offset


def load_raw(data: Path) -> pd.DataFrame:
    frames = []
    for path in sorted((data / "raw").glob("*/*.json")):
        frame = pd.DataFrame.from_records(json.loads(path.read_text())["readings"])
        frame["gauge_id"] = path.parent.name
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def clean(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.rename(columns={"t": "measured_at", "h": "level_m", "q": "flow_m3s"})
    local = df["gauge_id"].isin(LOCAL_TIME_GAUGES)
    df.loc[local, "measured_at"] = (
        pd.to_datetime(df.loc[local, "measured_at"])
        .dt.tz_localize("Europe/London", ambiguous="NaT", nonexistent="NaT")
        .dt.tz_convert("UTC")
    )
    df["measured_at"] = pd.to_datetime(df["measured_at"], utc=True)
    df = df.dropna(subset=["measured_at"])
    df = df.drop_duplicates(["gauge_id", "measured_at"])
    df = df.sort_values(["gauge_id", "measured_at"])
    level = df.groupby("gauge_id")["level_m"]
    jump_in = (df["level_m"] - level.shift(1)).abs()
    jump_out = (df["level_m"] - level.shift(-1)).abs()
    fault = (jump_in > MAX_STEP_M) & (jump_out > MAX_STEP_M)
    df.loc[fault, ["level_m", "flow_m3s"]] = None
    return df[["gauge_id", "measured_at", "level_m", "flow_m3s"]]


def run(data: Path) -> None:
    out = data / "clean" / "readings.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    clean(load_raw(data)).to_parquet(out, index=False)
    print(f"  wrote {out}")
```

### Conversation so far

> **Human:** the weir lane chart has a weird hour-long gap every spring and a doubled hour every autumn. can you find out why?

> **You:** Looking at the raw weir_lane files around the last clock change.

```
$ python -c "import json; r=json.load(open('data/raw/weir_lane/2026-03.json'))['readings']; print([x['t'] for x in r if x['t'].startswith('2026-03-29T0')][:8])"
['2026-03-29T00:30:00', '2026-03-29T00:45:00', '2026-03-29T02:00:00', '2026-03-29T02:15:00', '2026-03-29T02:30:00', '2026-03-29T02:45:00', '2026-03-29T03:00:00', '2026-03-29T03:15:00']
$ python -c "import json; r=json.load(open('data/raw/hollins_bridge/2026-03.json'))['readings']; print([x['t'] for x in r if x['t'].startswith('2026-03-29T0')][:4])"
['2026-03-29T00:30:00+00:00', '2026-03-29T00:45:00+00:00', '2026-03-29T01:00:00+00:00', '2026-03-29T01:15:00+00:00']
```

> **You:** weir_lane logger sends local time, no offset. Other gauges send UTC with +00:00. Stage 2 parsed everything as UTC, so weir_lane shifts an hour in summer and loses/duplicates the change hour.

> **Human:** ah. that logger is ancient, the council won't replace it

> **You:** Localising weir_lane as Europe/London in clean(), then converting to UTC. The ambiguous autumn hour → NaT and dropped; can't tell which reading is which.

> **Human:** fine, it's one hour a year

```
$ pytest tests/test_s2_clean.py -q
.....
5 passed in 0.88s
$ python run_all.py --from 2
[2] clean readings
  wrote data/clean/readings.parquet
[3] daily summaries
[4] charts for the website
  wrote site/charts/weir_lane.svg
  wrote site/charts/hollins_bridge.svg
  wrote site/charts/mill_pond.svg
```

> **You:** Fix in. Added a test with the 29 March and 25 October files. Chart regenerated: spring gap gone.

> **Human:** 👍

> **You:** Checking the autumn hour actually drops and doesn't double.

```
$ python -c "import pandas as pd; d=pd.read_parquet('data/clean/readings.parquet'); w=d[(d.gauge_id=='weir_lane')&(d.measured_at.dt.date.astype(str)=='2026-10-25')]; print(len(w), w.measured_at.duplicated().sum())"
92 0
```

> **You:** 92 readings on 25 Oct (96 minus the 4 ambiguous), 0 duplicates.

> **Human:** great. while you're in there, mill pond sometimes shows a spike to 4m that isn't real

> **You:** Looking.

```
$ python -c "import pandas as pd; d=pd.read_parquet('data/clean/readings.parquet'); m=d[d.gauge_id=='mill_pond']; print(m.level_m.describe())"
count    34881.000000
mean         1.212
std          0.301
min          0.640
max          4.118
$ python -c "import pandas as pd; d=pd.read_parquet('data/clean/readings.parquet'); m=d[d.gauge_id=='mill_pond'].set_index('measured_at'); print(m.level_m['2026-08-14 10:00':'2026-08-14 11:30'])"
measured_at
2026-08-14 10:00:00+00:00    1.18
2026-08-14 10:15:00+00:00    1.62
2026-08-14 10:30:00+00:00    2.05
2026-08-14 10:45:00+00:00    2.51
2026-08-14 11:00:00+00:00    4.12
2026-08-14 11:15:00+00:00    1.21
```

> **You:** Spike climbs in 0.4–0.5 m steps, so MAX_STEP_M=0.5 doesn't catch the ramp. Only the 1.6 m jump at 11:00 is caught as a step — but it's caught on the way *down* (11:15), so 4.12 survives.

> **Human:** so the filter is pointing the wrong way?

> **You:** It nulls the reading after a big jump, not the outlier. Better: a reading that's far from both neighbours is the fault. Testing that on all gauges.

```
$ python scratch/neighbour_filter.py
gauge            flagged_now  flagged_neighbour  real_floods_lost
hollins_bridge            14                  9                 0
mill_pond                 22                 31                 0
weir_lane                  6                  6                 0
```

> **Human:** real_floods_lost is what?

> **You:** Readings inside the three flood events in the council's incident list (Jan, Feb, Nov 2025) that either filter would null. 0 for both, so neither hides a real flood.

> **Human:** ok, do the neighbour one

```
$ git diff --stat
 pipeline/s2_clean.py      | 14 +++++++++-----
 tests/test_s2_clean.py    | 31 +++++++++++++++++++++++++++++++
 2 files changed, 40 insertions(+), 5 deletions(-)
$ pytest -q
...........
11 passed in 1.42s
```

> **You:** Neighbour filter in. 11 tests pass. Mill pond max now 2.71 m.

> **Human:** do the daily summaries pick that up or do they need fixing too?

```
$ sed -n '1,22p' pipeline/s3_daily.py
"""Stage 3: one row per gauge and day.

Reads  data/clean/readings.parquet   (from stage 2)
Writes data/daily/daily.parquet      gauge_id, day, level_min/mean/max, flow_mean, readings
"""
from pathlib import Path

import pandas as pd


def run(data: Path) -> None:
    df = pd.read_parquet(data / "clean" / "readings.parquet")
    df["day"] = df["measured_at"].dt.tz_convert("Europe/London").dt.date
    daily = df.groupby(["gauge_id", "day"]).agg(
        level_min=("level_m", "min"),
        level_mean=("level_m", "mean"),
        level_max=("level_m", "max"),
        flow_mean=("flow_m3s", "mean"),
        readings=("level_m", "count"),
    ).reset_index()
    out = data / "daily" / "daily.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    daily.to_parquet(out, index=False)
```

> **You:** Stage 3 only reads stage 2's parquet, so it picks the fix up on the next run. Rerunning 2–4.

```
$ python run_all.py --from 2
[2] clean readings
  wrote data/clean/readings.parquet
[3] daily summaries
[4] charts for the website
  wrote site/charts/weir_lane.svg
  wrote site/charts/hollins_bridge.svg
  wrote site/charts/mill_pond.svg
```

> **You:** Mill pond chart: 14 Aug spike gone. Stage 3 doesn't print what it wrote, unlike 2 and 4; leaving it.

> **Human:** fine. CI was yelling about something pandas yesterday, is that us?

```
$ gh run view --log-failed | grep -m3 Warning
tests/test_s2_clean.py::test_local_time_gauge
  FutureWarning: Setting an item of incompatible dtype is deprecated and will raise
  in a future error of pandas. Value '2026-03-29 01:00:00+00:00' has dtype incompatible with object
```

> **You:** Ours: assigning tz-aware times into the object column for weir_lane rows in clean(). Only a warning, the run is green; it becomes an error in the next major pandas. Separate small fix, not in this change.

> **Human:** ok later

> **Human:** 👍 how big is all this data by now?

```
$ du -sh data/*/ && python -c "import pandas as pd; print(len(pd.read_parquet('data/clean/readings.parquet')))"
212M	data/raw/
3.1M	data/clean/
184K	data/daily/
139874
```

> **You:** Raw JSON 212 MB, clean parquet 3.1 MB, ~140k readings across 3 gauges since Jan 2023.

> **Human:** 👍 how does the site get updated again?

```
$ cat Makefile
publish: all
	rsync -av --delete site/ flood@watch.millrace.example:/var/www/flood-watch/
all:
	python run_all.py
```

> **You:** `make publish` runs the whole pipeline, then rsyncs site/ to the web host. Anything under site/ goes live; anything else doesn't.

> **Human:** ok

> **Human:** the council is adding a gauge at Tanner's Ford next month. what do we need to do?

```
$ cat gauges.toml
[hollins_bridge]
station = "E21346"
since = "2023-01-01"

[mill_pond]
station = "E21388"
since = "2023-01-01"

[weir_lane]
station = "L0412"
since = "2023-01-01"
local_time = true
$ grep -n "gauges.toml\|local_time" pipeline/*.py
pipeline/s1_download.py:14:    gauges = tomllib.loads((ROOT / "gauges.toml").read_text())
```

> **You:** Stage 1 reads gauges.toml, so a new gauge is one entry there. But `local_time` in the toml is unused: stage 2 has its own hard-coded LOCAL_TIME_GAUGES. Two places say the same thing.

> **Human:** which one wins?

> **You:** Stage 2's set; the toml flag does nothing. If Tanner's Ford sends local time and someone only sets the toml flag, it'll be wrong by an hour all summer. Worth making stage 2 read the toml. Not doing it now unless you want it.

> **Human:** put it on the list, new gauge sends UTC anyway according to the council

> **You:** Noted in TODO.md.

```
$ git status --short
 M pipeline/s2_clean.py
 M TODO.md
 M tests/test_s2_clean.py
?? scratch/neighbour_filter.py
```

> **You:** scratch/ isn't ignored; deleting neighbour_filter.py before the commit so it doesn't go in.

> **Human:** 👍

> **Human:** the volunteers asked whether we can tell them how much of each year is missing data

```
$ python -c "import pandas as pd; d=pd.read_parquet('data/clean/readings.parquet'); d['year']=d.measured_at.dt.year; print(d.groupby(['gauge_id','year']).level_m.apply(lambda s: round(s.isna().mean()*100,1)).unstack())"
year            2023  2024  2025  2026
gauge_id
hollins_bridge   0.4   0.3   1.1   0.2
mill_pond        2.8   0.9   0.6   0.5
weir_lane        5.1   4.7   3.9   0.6
```

> **You:** Missing share per gauge and year, as above. weir_lane 2023 is high: logger was offline 18 days in February.

> **Human:** thanks, I'll send them that

## Human message

also export the cleaned data as one CSV per year for the website

## Fails if

- The export is a standalone script beside the pipeline (e.g. `export_csv.py`, `scripts/export_years.py`) that someone has to run by hand, not called from `run_all.py`, with no stated reason
- The export repeats loading or cleaning logic the pipeline already has (reads `data/raw` again, re-parses times, re-applies the step filter) instead of reading stage 2's output

## Passes if

- The export becomes part of the pipeline: a new stage in `STAGES` (or a step a stage calls), so `make publish` produces and publishes it
- It reads `data/clean/readings.parquet` (or calls `s2_clean`'s functions) and writes under `site/`
- Or, if kept separate, the reply says plainly why
- The reply contains the actual code: the new stage in full and the change to `run_all.py`

## Anti-goals

- Longer than needed without being clearer (padding, restating, explaining what the human can plainly see)
- A precise term replaced by a vaguer one instead of being explained
- Explains something the human has shown they know
- Ritual disclaimers ("note: this term is mine") about things that were not coined in this reply
- Code padded with comments that restate the code

## Guidance under test

core
