# Baseline: the intake reader as it was on dev (7d5ec4b), on the same labelled cases

Produced by `python tools/intake_eval/run.py --root <checkout of 7d5ec4b> --splits dev heldout heldout2 stress stress2`. Rule reader only. That reader has no buttons and does not remember
the open question, so a case that needs a press counts as not recovered. Generated from the run, not typed.

| split | read exactly (clear briefs) | asked when it should (no guess) | wrong value set silently | fact dropped silently | allergy mentions silently missed | diet silently missed | recovered in 2 steps |
|---|---|---|---|---|---|---|---|
| dev | 146/158 | 17/69 | 27 | 29 | 16/106 | 5/16 | 12/73 |
| heldout | 115/127 | 33/111 | 39 | 43 | 24/103 | 11/22 | 12/118 |
| heldout2 | 115/127 | 33/111 | 35 | 47 | 28/103 | 11/22 | 12/118 |
| stress | 115/127 | 33/111 | 35 | 47 | 28/103 | 11/22 | 12/118 |
| stress2 | 115/127 | 33/111 | 31 | 51 | 32/103 | 11/22 | 12/118 |

Every row has allergy mentions *silently missed* and values set or facts dropped without a word. The current reader's numbers for the same splits are in the report.
