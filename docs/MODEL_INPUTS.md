# What each model stage is sent

Models are optional (`EVENTMATE_ENABLE_LLM=0` turns them off). Contact details are replaced by placeholders (`<EMAIL_1>`, `<PHONE_1>`) before any prompt is built; the map stays in the process and is never logged or sent.

| Stage | Input | Caps | Masked before sending |
|---|---|---|---|
| Intake | the user's message, the reviewed draft, today's date, subtype names | message 2000 chars; up to 600 tokens out | emails, phone numbers (placeholders) |
| Assistant | the question, a tool catalogue, tool results, the last 4 question/answer pairs (fenced, untrusted) | question 1000 chars; 4 steps, 700 tokens out | emails, phone numbers; tool results also mask PAN, Aadhaar, card numbers |
| Ideas | eight facts: event type, guest count, budget band, diets, city, venue setting, up to 5 preferences, subtype | 900 tokens out | preferences pass through the same placeholder step |
| Explanation | the verified plan summary (vendor names, numbers) | 3000 tokens out | no raw request and no guest names are in it |

The assistant's `get_event_state` tool returns a bounded slice of EventState (docs/EVENT_STATE.md): the event date, RSVP counts, each vendor's category, name, booking status, conversation status and whether a vendor price is only quoted (never the amount), and counts of open items by kind. No guest names, contact details, prices or message text.
The assistant's `get_guest_counts` tool returns the imported guest list as counts only (docs/GUEST_IMPORT.md): people who said yes, no or have not answered, how many need each diet, and whether the list agrees with the planned headcount. Never a name, a contact detail or a raw cell; a guest list's names are kept in one private file for the plan and appear in no prompt, log or trace.

A voice turn sends the assistant exactly what typed chat would: the transcript, masked and checked the same way, never the audio. A voice session keeps its own short conversation memory (docs/VOICE.md); a spoken answer is the already-checked answer rewritten for speech and is not sent anywhere.

Not sent to any model: the budget total or ledger (except as numbers a tool returns to the assistant), vendor reply text, discovered contact text, secrets.
Optional Langfuse export, if configured, sends masked questions and answers off the machine.

## Normalisation and the rule for any future model input (Phase 4)

Before a typed or spoken question, an intake message, a language-search request or an idea preference is screened, masked, cached or sent, it goes through `eventmate/untrusted.py`: Unicode NFKC, zero-width, bidi, soft-hyphen and tag characters removed, digits of any script turned into ASCII, whitespace collapsed. The normalised text is what is screened, masked and sent on; the original is not kept. A text that hides more than 8 such characters (or more than 32 joiners) is refused with a plain message instead of being cleaned in silence, and a model is not called for it.

Names that come from outside (OpenStreetMap, search results, free-text fields) pass `sanitize_label` when they are stored: normalised, one line, tags and markup characters removed, fence tokens removed, 120 characters at most. Every prompt fills its placeholders in one pass (`render_template`), so a value that contains `{tools}` or `{message}` stays literal text. The explainer's plan text has the fence tokens removed before it enters its prompt.

**What the tests show.** `tests/test_llm_boundary.py` proves the boundary against a model that does whatever it is told: it cannot use a tool its asker lacks (same words as an unknown tool), cannot get a hostile argument past the schema, cannot name another plan, cannot claim a proposal was applied, cannot add a link, address or number the evidence lacks, is stopped at the step cap, has no route to approve anything, and the approval text is built by code. `tests/test_untrusted.py` adds visibility only: hidden characters, look-alike letters, spoken addresses, digit tricks and several injection phrasings are flagged or masked. Both lists are incomplete, because a regular expression cannot recognise a paraphrased, translated or encoded injection, and the scan never decides anything by itself. Nothing here was run against a real model.

**Rule for any future model input.** Text that a third party can write (a vendor reply, a web page, a contact page, an uploaded CSV cell, a voice transcript, an imported archive) may reach a model only if all of these hold: it is normalised and fenced; the model's output is schema-validated and cannot call tools in that context; its output is never shown as a fact without the answer fact check; and a test with an obedient fake model proves the first three. Guest CSV cells and imported archive text never reach a model (counts only). Today no vendor reply, contact text or fetched page is sent to any model.

**Limits per workspace.** Every model call is charged to the workspace bound to the request: calls per hour (`EVENTMATE_WS_LLM_CALLS_PER_HOUR`, default 200), estimated tokens per hour (`EVENTMATE_WS_LLM_TOKENS_PER_HOUR`, default 300000) and calls in flight (`EVENTMATE_WS_LLM_CONCURRENCY`, default 2). Failed calls count. `EVENTMATE_LLM_MAX_CALLS` stays the owner's ceiling above all workspaces. These defaults are my guesses for one person on a local machine; lower them before hosting. Token counts are estimates (four characters per token), not the provider's.
