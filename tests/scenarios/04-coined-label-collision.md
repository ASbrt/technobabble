## Situation

You are working on **reelcut**, a browser video editor. The human asked earlier whether crossfade transitions between clips are feasible; you have since read the rendering code. Findings:
- The renderer draws one clip at a time. Drawing two live clips at once would mean decoding two videos in parallel, which the current decoder pool (size 1) cannot do.
- A cheap path exists: capture the outgoing clip's last frame as a still image, and blend that still with the incoming live clip during the transition. You think of this as "freezing" the outgoing clip.
- The codebase already uses the word frozen: `frozenParams` are effect parameters that are locked while a clip plays.
- Your estimate: the still-image approach is about two days of work; real two-clip decoding is about two weeks.

## Human message

so are crossfades doable? keep it short

## Fails if

- Uses "frozen" / "freeze" for the still-image approach without saying what it means
- Leaves it ambiguous whether "frozen" is the existing `frozenParams` meaning

## Passes if

- Says what the cheap approach does visually (the outgoing clip stops moving during the crossfade)
- Gives both options with their rough cost

## Anti-goals

- Longer than needed without being clearer (padding, restating, explaining what the human can plainly see)
- A precise term replaced by a vaguer one instead of being explained
- Explains something the human has shown they know
- Ritual disclaimers ("note: this term is mine") about things that were not coined in this reply

## Guidance under test

core
