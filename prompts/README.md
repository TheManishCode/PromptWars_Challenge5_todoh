# Prompts

Versioned Markdown prompts for EventMate agents live here, one file per prompt,
named `<agent>.<purpose>.v<N>.md`. A prompt is never edited in place after it
is used in a run: add `v<N+1>` instead, so logged LLM traces stay reproducible.

Rules:

- Prompts carry wording and output format only. No business logic, thresholds,
  prices, availability, capacities or booking status.
- Prompts only ever receive data fenced between markers, and user text has those markers stripped.
- `intake.extract.v1.md`: reads the organiser's words into a fixed JSON shape. The reply is untrusted data: it is schema-validated, never executed, and a human approves the result before anything is planned.
- `delta.extract.v1.md`: reads a change request into an allow-listed JSON delta. The reply is untrusted data: unknown keys are dropped, numbers and dates stand only if the user wrote them, ids only if they are in the plan, and the user confirms the delta before anything changes.
- `assistant.system.v1.md`: the assistant's rules and reply format. Pinned by `assistant.lock.json`; a test fails if it changes without `python -m eventmate.assistant.evals --update-lock`.

