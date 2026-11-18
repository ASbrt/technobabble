Yes, crossfades are doable. There are two ways to do it:

- **Cheap version, about 2 days:** grab the last frame of the outgoing clip as a still image and fade it into the incoming clip as that clip plays. The catch: the outgoing clip stops moving during the transition. With short fades most people won't notice.
- **Full version, about 2 weeks:** both clips keep playing during the fade. That means decoding two videos at once, and right now the renderer can only decode one at a time, so that part has to be rebuilt.

I'd start with the cheap version and only do the full one if the paused frame looks wrong in practice. Want me to go ahead with it?

## Verdict

PASS — no `Fails if` line applies; every `Passes if` line holds; no anti-goal tripped.
