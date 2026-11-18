# Triggering

Whether `writing-docs` loads at the right moments. Each request went to a fresh subagent with the list of available skills (superpowers' 15 and `technobabble:writing-docs`, names and descriptions only) and the question which it would load before starting. One run per request, 2026-11-18.

| Request | Should load writing-docs | Loaded |
|---|---|---|
| Write a README for the src/importer folder. | yes | technobabble:writing-docs |
| Record our decision to switch from Redis to Postgres LISTEN/NOTIFY as an ADR. | yes | technobabble:writing-docs |
| The glossary is out of date, add the terms epoch and ord. | yes | technobabble:writing-docs |
| Tighten up docs/DEPLOYMENT.md, it has gotten long and repetitive. | yes | technobabble:writing-docs |
| Fix the typo in the error message in importer.py. | no | superpowers:test-driven-development |
| Write an implementation plan for the invoice export feature. | no | superpowers:writing-plans |
| Create a new skill for our release checklist. | no | superpowers:brainstorming, superpowers:writing-skills |
| Add a docstring to the next_event_ord() function. | no | NONE |

8 of 8 as intended.
