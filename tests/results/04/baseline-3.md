Yes, there's a cheap way to do them.

- **Cheap version, about 2 days:** at the start of the transition, grab the last frame of the outgoing clip as a still picture and fade it into the incoming clip, which keeps playing. The catch is that the outgoing clip stops moving during the fade. With short fades (under about half a second) most people won't notice.
- **Full version, about 2 weeks:** both clips keep playing while they fade. The renderer only decodes one video at a time right now, so this means reworking it to decode two at once.

I'd start with the cheap version and only do the full one if the paused outgoing clip looks wrong. Want me to go ahead with the 2-day version?

## Verdict

PASS — no `Fails if` line applies; every `Passes if` line holds; no anti-goal tripped.
