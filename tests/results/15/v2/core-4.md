One catch before you merge: the control sheet is not in the PR.

What #12 has now, all approved and green: the reconnect backoff with the "Lost the show computer — tap to retry" banner, the play fix (a repeated play for the cue that is already running is ignored), and the pairing code no longer logged. Those are the 4 commits I pushed today, on top of the 9 that were already there.

The control sheet (the slide-up panel with the volume slider and scene buttons from yesterday's session) was never committed. Its files are still sitting as uncommitted changes on my machine: `ControlSheet.tsx`, `SceneButtons.tsx`, `VolumeSlider.tsx`, `RemoteScreen.tsx`, `remote.css` and `useShowState.ts`. What you tried on your phone last night was the dev build running from my working copy, not the branch. If you merge tonight, the remote will ship without the sheet.

I haven't looked at those changes yet today, so I can't yet say whether they are exactly yesterday's sheet and nothing else, or whether their tests pass.

Two ways to go:

1. **Add it to #12.** I check the diff, run the tests and lint, commit and push. CI runs again, and the reviewer bot will most likely re-review the new commit, so you'd be waiting on its approval again before merging tonight.
2. **Merge #12 as it is, sheet in its own PR.** #12 stays approved and green and can go in tonight; the sheet follows in a small separate PR, ideally still before Friday's show.

Which do you want?

## Verdict

PASS (draft 1) — says the six files are untested.
