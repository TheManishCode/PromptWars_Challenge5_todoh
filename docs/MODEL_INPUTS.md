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
