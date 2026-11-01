# Guest-list import

Upload a CSV of your guests for one plan, check it, then save it. The list holds names, so it is handled narrowly (ADR-055).

## What it does and does not do

- It checks the file, shows you every problem by line, and saves nothing until you commit.
- It shows the list's totals **beside** the headcount you stated. The plan keeps the number you stated; the list never changes it. A mismatch is flagged.
- It never messages a guest. There is no invitation or reminder feature.
- It does **not** change the saved plan. Diet counts and RSVPs from the list are shown and available to the assistant, but a plan version is immutable: the caterer's diet check still uses the diets in the plan. Applying a list to a new plan version is a separate step that is not built yet.
- It never stores guest emails, phone numbers or addresses. A column with one of those names is refused, with the reason.

## The file

UTF-8 CSV, up to 256 KB and 500 rows. The first line names the columns, using only these, each at most once:

| Column | Meaning | Blank means |
|---|---|---|
| `name` | At most 120 characters | `Guest 1`, `Guest 2`, ... |
| `party_size` | Whole number 1 to 20 | 1 |
| `rsvp` | `yes`, `no` or `pending` (any case) | pending |
| `dietary` | Semicolon list from `vegetarian`, `non_vegetarian`, `vegan`, `jain`, `halal`, `gluten_free` | none |

```csv
name,party_size,rsvp,dietary
Asha Kulkarni,3,yes,vegetarian
Rohan Deshpande,2,yes,vegetarian;jain
Meera Iyer,1,pending,
```

Rules:

- A cell that starts with `=`, `+`, `-` or `@` (after control characters and spaces are removed) is rejected: a spreadsheet would run it as a formula. A dash inside a name is fine.
- Control characters are removed from cells. A file containing a NUL byte, or not valid UTF-8, is refused as a whole.
- A cell over 400 characters, a row with more cells than the header, and an unknown diet, RSVP or party size are row errors. Error messages never repeat what a cell contained.
- Blank lines are ignored. A header with no rows is an empty list, which cannot be saved.

## Endpoints

All need the CSRF header and the local-origin rules of the other write routes, and the plan must exist.

```
POST /api/plans/<id>/guests/preview     JSON {"csv": "<text>"}   or   multipart/form-data with one file in the field "file"
-> 200 {"can_commit": true, "preview_token": "<single-use token>" | null,
        "row_errors": [{"line": 4, "column": "party_size", "message": "..."}],
        "totals": {"rows": 4, "heads": 10, "yes": 5, "no": 1, "pending": 4, "dietary_counts": {"vegetarian": 2}, "required_diets": ["vegetarian"]},
        "guests": [...], "check": {"planning_headcount": 120, "expected": 9, "flag": "the list accounts for 9 of the 120 people you planned for; the plan keeps 120"}, "notice": "..."}
-> 400 the file cannot be used (not UTF-8, wrong header, too many rows)   -> 413 over 256 KB
```
`can_commit` is false and `preview_token` is `null` while any row has a problem: a file with errors cannot be saved. `heads` counts people (party sizes), declined included. The rows in the preview are for your own screen. These are the names the page reads; no older alias names are emitted.

```
POST /api/plans/<id>/guests/commit      {"preview_token": "<token>"}
-> 201 {"saved": true, "totals": {...}, "check": {...}}      -> 409 the preview is no longer valid; preview the file again
```
The token works once, for that plan, for 15 minutes. It stops working if a newer preview was taken for the plan, or the list was changed or deleted since. At most eight previews are held; they live in memory only.

```
GET    /api/plans/<id>/guests           -> {"has_list": true, "totals": {...}, "check": {...}}   (totals only, never names)
DELETE /api/plans/<id>/guests           body {}   -> {"deleted": true|false}
```

Counting: people are counted by party size. A declined party is not counted towards diets (the same rule the Guest Agent uses). `expected` is yes plus pending.

## Where the names live

One file per plan, `guests/guests-<plan_id>.json`, owner-only (0600), replaced atomically on commit. Nothing else holds a name:

- Not the decision log (it records only that a list was saved or deleted, with the count of people).
- Not model prompts, traces or the Langfuse export: the assistant's `get_guest_counts` tool returns counts and diet totals only (docs/MODEL_INPUTS.md).
- Not EventState: with a list saved, the RSVP facts and the pending-RSVP item use the list's counts.

Deleting the list, or the plan (docs/RETENTION.md), overwrites the file with zeros and removes it, and drops any preview waiting for a commit.

## Not covered

Names are personal data on your disk: backups you make of the plan directory contain them. Free-text names are not masked in the question you type to the assistant, so asking it about a guest by name sends that name to the model; the import itself never does. Importing from a spreadsheet file (xlsx), merging two lists, and editing a saved list in place are not built: replace the list by importing again.
