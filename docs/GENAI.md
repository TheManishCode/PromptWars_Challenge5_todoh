# Where the model is used, and where it is not

The product is a planning assistant for weddings, birthdays, community events and similar. The plan itself (vendors, budget, timeline) comes from software that can be checked: rules, a constraint solver (OR-Tools CP-SAT), Fast Downward with the VAL validator, and a validator that refuses a plan that breaks a constraint. A language model helps around that core and never has authority over it.

| Where | What the model does | What stops it going wrong | If there is no model |
|---|---|---|---|
| Intake (`planning/intake.py`) | turns what the organiser wrote into a draft: event type, city, date, guests, budget, diet | the draft is only a proposal the person confirms on a review card; a value the parser cannot corroborate is flagged; missing facts become a question | a deterministic parser reads the same fields; text it cannot read gets a question |
| Ideas (`planning/ideas.py`) | personalised suggestions (themes, activities, menu notes) from the event's facts | ideas are labelled as ideas, are screened, and cannot change the plan | template ideas from `data/idea_templates.json` |
| Explainer (`agents/explainer.py`) | rewrites the plan's reasons in plain words | every fact must come from the plan; the rewrite is refused if it adds a number, name or link | the plain deterministic explanation |
| Assistant (`assistant/`) | answers questions about the plan and files proposals (a payment, a note) | tools only file proposals; a human approves an exact content hash; answers may state nothing the evidence did not supply; a limit on calls and tokens per workspace | documented no-model answers |
| Explore (`integrations/explore.py`) | routes a free-text search to a public place or deal lookup | outside text is normalised, screened and treated as data | forms still work |

Every prompt is a versioned file in `prompts/`; `prompts/assistant.lock.json` pins the hash of the prompt that passed the evals, and a test fails if the prompt changes without the lock being regenerated.

## Evidence, and its limits
- 20 behavioural cases (`eventmate/assistant/eval_cases.json`) and a retrieval eval run through `python -m eventmate.assistant.evals`. All 20 hold on the no-model path in the test suite. **They have not been run against a real model by the builders** (`behavioral: not_run` in the lock): there was no key in the build environment. The `live-evals` workflow runs them with the repository's own key.
- An outside verifier ran 24 live probe cases against a free model once (see `docs/REDTEAM.md`, Pass 3).
- The demonstration catalogue behind the plans is synthetic (nine cities). The model quality claims above are about guard rails, not about how good the suggestions are: nobody has rated them.
