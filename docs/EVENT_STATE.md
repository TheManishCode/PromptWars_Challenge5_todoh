# EventState: where a plan stands

A read-only view built on demand from the records a plan already has: its latest version, ledger, vendor messages, quote jobs and guest counts. It calls no model and no network, writes nothing and adds no database. It approves, sends and spends nothing: seeing that something is waiting is not permission to act on it (ADR-054).

```
GET /api/plans/<plan_id>/state
GET /api/plans/<plan_id>/state?changes_since=2026-11-01T09:00:00+05:30
-> 200 EventState      -> 400 changes_since is not an ISO 8601 time      -> 404 no such plan
```

Illustrative values (the shape is real; the figures are made up for the example):

```json
{
  "plan_id": "f63ca79a6d19", "plan_version": 1, "as_of": "2026-11-01T09:30:00Z",
  "facts": [
    {"key": "guest_count", "label": "Guests you planned for", "value": 120, "source": "user", "as_of": "2026-11-01T09:00:00Z", "status": "user_stated"},
    {"key": "plan_cost_inr", "label": "Planned vendor cost (INR)", "value": 139000, "source": "tool", "as_of": "2026-11-01T09:00:00Z", "status": "pending"}
  ],
  "vendors": [
    {"category": "caterer", "vendor_id": "cat-pune-rasoi", "name": "Royal Rasoi", "booking_status": "available", "plan_cost": 72000, "price_stale": false,
     "conversation": "replied", "last_activity_at": "2026-11-01T09:20:00Z",
     "quote": {"key": "quote:cat-pune-rasoi", "label": "caterer price the vendor wrote (INR, per_plate, as they wrote it)", "value": 650,
               "source": "vendor_reply", "as_of": "2026-11-01T09:20:00Z", "status": "quoted_unverified"}}
  ],
  "open_items": [
    {"kind": "unconfirmed_quote", "ref": "msg-1", "label": "Royal Rasoi mentioned a price that is not recorded", "since": "2026-11-01T09:20:00Z",
     "age_hours": 0, "closes_when": "you record the price in the ledger, or mark the reply read"}
  ],
  "changes": [{"at": "2026-11-01T09:20:00Z", "kind": "reply", "summary": "Royal Rasoi replied and mentioned a price (not recorded)"}],
  "notice": "A read-only view assembled from this plan's records. ..."
}
```

## Facts

Every fact carries where it came from and how far to trust it.

| `source` | Meaning |
|---|---|
| `user` | you typed it, recorded it or confirmed it |
| `vendor_reply` | words a vendor wrote to you |
| `tool` | computed by this application from other facts |
| `seed_data` | the bundled demo data (for example the demo guest lists) |
| `model_idea` | reserved: a model suggestion; nothing in the state uses it today |

| `status` | Meaning |
|---|---|
| `user_stated` | what you told it |
| `quoted_unverified` | a price a vendor wrote. It is never a quote until you record it, and it never reprices the plan (ADR-049) |
| `confirmed` | a booking you confirmed, or a vendor price you confirmed in a quote job |
| `pending` | an estimate or a count still open (for example the planned cost before anything is booked) |

When you record a quote in the ledger it replaces the vendor's wording as the price that counts (`source: user`, `status: user_stated`). A payment or a quote for a different category is not a quote for this vendor. A reply from an address other than the one you wrote to is never shown as the vendor's price.

When an imported guest list is saved (docs/GUEST_IMPORT.md), the `rsvp_*` facts and the `pending_rsvps` item use the list's counts instead of the plan's, and a `guest_list_check` fact says when the list and your planned headcount disagree.

## Vendor conversation status

`none`, `awaiting_approval` (drafted, not approved), `asked` (sent, no reply), `replied`, `unavailable` (the vendor says it cannot do the date), `stopped` (the vendor asked not to be contacted again), `problem` (approved or sending, or failed, denied, expired, blocked, unknown outcome: it needs a look).

## Open items: nothing left hanging

Oldest first. Each says how old it is and what closes it.

| `kind` | When it is open | What closes it |
|---|---|---|
| `pending_rsvps` | Guests are still pending. Age counts from the plan's first version. With no guest list loaded the label says replies are not being tracked | The guest list is updated and nobody is pending |
| `approval_waiting` | A drafted message is not approved, denied or expired | You approve or deny it, or it expires |
| `unanswered_vendor` | A request was sent and nothing came back | The vendor replies |
| `unconfirmed_quote` | A reply mentioned a price you have not recorded or marked read | You record it in the ledger, or mark the reply read |
| `follow_up_due` | In a running quote job, the reply window has passed, nudges remain and none is waiting for approval | A follow-up is sent or approved, or the vendor replies |
| `job_item` | A quote job has an item you have not resolved. The label is a fixed sentence, not the vendor's words | You resolve it in the job |

## Changes

`changes` is newest first, at most 50, from the records' own timestamps: plan versions, ledger entries, messages drafted, sent, denied or expired, vendor replies and quote-job events. The run logs are per run and not per plan, so they are not the source. `changes_since` keeps only entries strictly newer than the time given (a time without a zone is read as UTC).

## What it never contains

Guest names, email addresses, phone numbers, message bodies, vendor words beyond the one quoted amount and its unit. A test checks this against a plan that uses a named guest list and a reply containing a phone number and an email address.

## Assistant

The assistant has one read-only tool, `get_event_state`, returning only: the event date, RSVP counts, each vendor's category, name, booking status and conversation status, whether its price is only quoted (the status, not the amount), and counts of open items by kind with the oldest age. The assistant's answer is still checked against this turn's tool results only.

## Deleting a plan

Deleting a plan (docs/RETENTION.md) removes everything the state is built from, so the state is gone with it.
