"""Ideas, digests, notifications, vendor search, contacts, suppression and community checks."""

from __future__ import annotations

import json
import re
from flask import jsonify, request
from eventmate.schemas import CommunityDetails, VendorCategory
from eventmate.sourcing.search import SourceNotSupported
from eventmate.web.base import (
    QuoteConfirmBody,
    QuotePromoteBody,
    PasteReplyBody,
    UserVendorBody,
    QuoteRetractBody,
    AccessBody,
    AssignReplyBody,
    ByBody,
    CommunityItemBody,
    ConfirmContactBody,
    ContactBody,
    DoneBody,
    EmptyBody,
    IdeasBody,
    LiftBody,
    NotifyConfirmBody,
    NotifyProposeBody,
    ShortlistSearchBody,
    SuppressBody,
    TestVendorBody,
    WebsiteBody,
)


def register(app, k):
    P = k.P
    assistant = k.assistant
    body = k.body
    em = k.em
    lock = k.lock
    out = k.out
    pid = k.pid
    today_fn = k.today_fn

    @app.get("/api/plans/<plan_id>/ideas")
    def ideas_get(plan_id):
        plan_id = pid(plan_id)
        with lock:
            em.store.latest(plan_id)
            v = em.ideas.view(plan_id)
        return jsonify({
            "ideas": [json.loads(i.model_dump_json()) for i in v.visible], "dismissed": list(v.dismissed), "pinned": list(v.pinned),
            "notes": [json.loads(n.model_dump_json()) for n in v.notes], "generated_at": v.generated_at.isoformat() if v.generated_at else None,
            "source": v.source, "facts_used": list(v.facts_used), "notice": v.notice,
        })

    @app.post("/api/plans/<plan_id>/ideas")
    def ideas_generate(plan_id):
        """Personalized suggestions for this plan. Labelled as suggestions: nothing here is a verified offer or changes the plan."""
        plan_id, b = pid(plan_id), body(IdeasBody)
        with lock:
            result = em.ideas.generate(em.store.latest(plan_id), b.preferences)
            for pref in b.preferences:  # a preference typed on the form is a user-stated fact, with its source
                assistant.sessions.add_fact(plan_id, P().user_id, pref, "form:ideas.preferences")
        return out(result, 201)

    @app.post("/api/plans/<plan_id>/ideas/<idea_id>/pin")
    def ideas_pin(plan_id, idea_id):
        plan_id, idea_id = pid(plan_id), pid(idea_id)
        body(EmptyBody)
        with lock:
            em.store.latest(plan_id)
            note = em.ideas.pin(plan_id, idea_id)
        return out(note, 201)

    @app.post("/api/plans/<plan_id>/ideas/<idea_id>/dismiss")
    def ideas_dismiss(plan_id, idea_id):
        plan_id, idea_id = pid(plan_id), pid(idea_id)
        body(EmptyBody)
        with lock:
            em.store.latest(plan_id)
            em.ideas.dismiss(plan_id, idea_id)
        return jsonify({"dismissed": idea_id})

    @app.post("/api/plans/<plan_id>/ideas/<idea_id>/restore")
    def ideas_restore(plan_id, idea_id):
        plan_id, idea_id = pid(plan_id), pid(idea_id)
        body(EmptyBody)
        with lock:
            em.store.latest(plan_id)
            em.ideas.restore(plan_id, idea_id)
        return jsonify({"restored": idea_id})

    @app.post("/api/plans/<plan_id>/notes/<note_id>/done")
    def notes_done(plan_id, note_id):
        plan_id, note_id, b = pid(plan_id), pid(note_id), body(DoneBody)
        with lock:
            em.store.latest(plan_id)
            note = em.ideas.set_done(plan_id, note_id, b.done)
        return out(note)

    @app.get("/api/plans/<plan_id>/digests")
    def digests_get(plan_id):
        """What changed on this plan's vendor jobs and what needs you. Always kept in the app, newest first."""
        plan_id = pid(plan_id)
        with lock:
            em.store.latest(plan_id)
            items = em.digest_store.list(plan_id)
        return jsonify([json.loads(d.model_dump_json()) for d in items])

    @app.post("/api/plans/<plan_id>/digests/<digest_id>/read")
    def digest_read(plan_id, digest_id):
        plan_id, digest_id = pid(plan_id), pid(digest_id)
        body(EmptyBody)
        with lock:
            em.store.latest(plan_id)
            rec = em.digest_store.mark_read(plan_id, digest_id)
        return out(rec)

    @app.get("/api/notify")
    def notify_get():
        with lock:
            st = em.notifier.status()
        return out(st)

    @app.post("/api/notify/propose")
    def notify_propose():
        """Step one of the optional email copy: the exact address and what it may contain. Nothing is sent until the owner confirms."""
        b = body(NotifyProposeBody)
        with lock:
            p = em.notifier.propose(b.destination, b.include_vendor_names, b.include_amounts, b.include_raw_text)
        return out(p, 201)

    @app.post("/api/notify/confirm")
    def notify_confirm():
        b = body(NotifyConfirmBody)
        with lock:
            st = em.notifier.confirm(b.code)
        return out(st)

    @app.post("/api/notify/disable")
    def notify_disable():
        body(EmptyBody)
        with lock:
            st = em.notifier.disable()
        return out(st)

    @app.post("/api/plans/<plan_id>/shortlist/search")
    def shortlist_search(plan_id):
        """Find businesses for this plan's city and category. They are shortlisted, never added to the vendor database, and nothing is contacted."""
        plan_id, b = pid(plan_id), body(ShortlistSearchBody)
        with lock:
            try:
                res = em.search_vendors(plan_id, b.category, b.source, b.limit)
            except SourceNotSupported as exc:
                return jsonify({"status": "not_supported", "error": str(exc)}), 422
        return out(res)

    @app.get("/api/plans/<plan_id>/shortlist")
    def shortlist_get(plan_id):
        plan_id = pid(plan_id)
        with lock:
            items = em.shortlist_view(plan_id)
        return jsonify([json.loads(i.model_dump_json()) for i in items])

    @app.get("/api/plans/<plan_id>/vendor-cards")
    def vendor_cards_get(plan_id):
        """Every shortlisted business as a card: what the app holds, each fact with its source, date and whether you confirmed it. A Google listing's own details are not here: fetch them by place id."""
        plan_id = pid(plan_id)
        with lock:
            cards = em.vendor_cards(plan_id, today_fn())
        return jsonify({"cards": [json.loads(c.model_dump_json()) for c in cards]})

    @app.get("/api/plans/<plan_id>/quote-readings")
    def quote_readings_get(plan_id):
        """Each vendor reply read as a quote: the vendor's own line for every field, what you have confirmed, how complete it is, and what does not fit your event. Nothing here is recorded."""
        plan_id = pid(plan_id)
        with lock:
            found = em.quote_assessments(plan_id)
        return jsonify({"quotes": [json.loads(a.model_dump_json()) for a in found]})

    @app.get("/api/plans/<plan_id>/quote-comparison")
    def quote_comparison_get(plan_id):
        """Your fully confirmed quotes ranked by a rule fixed before any reply is opened. Reply order and speed play no part. Add ?closed=1 to rank now, before enough vendors have answered."""
        plan_id = pid(plan_id)
        with lock:
            res = em.compare_quotes(plan_id, request.args.get("closed") == "1")
        return out(res)

    @app.post("/api/plans/<plan_id>/quote-readings/<message_id>/confirm")
    def quote_readings_confirm(plan_id, message_id):
        """Confirm fields of a reply: as read, or with a value you typed. An amount is confirmed on its own. Confirming the price and how long it holds records the price as a quote."""
        plan_id, message_id, b = pid(plan_id), pid(message_id), body(QuoteConfirmBody)
        with lock:
            a = em.confirm_quote_fields(plan_id, message_id, b.fields, b.by, b.valid_days)
        return out(a, 201)

    @app.post("/api/plans/<plan_id>/quote-readings/<message_id>/promote")
    def quote_readings_promote(plan_id, message_id):
        """Let the planner choose this vendor, for your city and date only, from the quote you confirmed. Refused unless the reply is fully confirmed and its quote has not run out."""
        plan_id, message_id, b = pid(plan_id), pid(message_id), body(QuotePromoteBody)
        with lock:
            a = em.promote_quote(plan_id, message_id, b.by, b.setting)
        return out(a, 201)

    @app.post("/api/plans/<plan_id>/quote-readings/<message_id>/retract")
    def quote_readings_retract(plan_id, message_id):
        plan_id, message_id, b = pid(plan_id), pid(message_id), body(QuoteRetractBody)
        with lock:
            a = em.retract_quote_field(plan_id, message_id, b.field, b.by)
        return out(a)

    @app.get("/api/plans/<plan_id>/request-text")
    def request_text_get(plan_id):
        """The quote request as text, for you to send yourself from your own mail or WhatsApp. Built from the brief's facts alone; nothing is stored or sent."""
        plan_id = pid(plan_id)
        a = request.args
        try:
            category = VendorCategory(a.get("category", ""))
        except ValueError:
            return k._err(400, "category must be venue, caterer or photographer")
        with lock:
            subject, text = em.request_text(plan_id, category, a.get("name", "")[:120], a.get("sender", "")[:80])
        return jsonify({"subject": subject, "body": text})

    @app.post("/api/plans/<plan_id>/shortlist/<vendor_id>/website")
    def shortlist_website(plan_id, vendor_id):
        plan_id, vendor_id, b = pid(plan_id), pid(vendor_id), body(WebsiteBody)
        with lock:
            em._scope(plan_id)
            biz = em.set_business_website(vendor_id, b.url)
        return out(biz)

    @app.post("/api/plans/<plan_id>/shortlist/<vendor_id>/find-contacts")
    def shortlist_find_contacts(plan_id, vendor_id):
        """Read the business's own website for public email addresses. Found addresses are candidates; none is used until you confirm it."""
        plan_id, vendor_id = pid(plan_id), pid(vendor_id)
        body(EmptyBody)
        with lock:
            em._scope(plan_id)
            res = em.find_business_contacts(vendor_id)
        return out(res)

    @app.post("/api/plans/<plan_id>/shortlist/<vendor_id>/contacts/<candidate_id>/confirm")
    def shortlist_confirm(plan_id, vendor_id, candidate_id):
        plan_id, vendor_id, candidate_id, b = pid(plan_id), pid(vendor_id), pid(candidate_id), body(ConfirmContactBody)
        with lock:
            em._scope(plan_id)
            contact = em.confirm_contact(vendor_id, candidate_id, b.by, b.edited_value, b.acknowledge_bounce, b.business_name)
        return out(contact, 201)

    @app.post("/api/plans/<plan_id>/shortlist/<vendor_id>/contacts/<candidate_id>/reject")
    def shortlist_reject(plan_id, vendor_id, candidate_id):
        plan_id, vendor_id, candidate_id = pid(plan_id), pid(vendor_id), pid(candidate_id)
        body(EmptyBody)
        with lock:
            em._scope(plan_id)
            cand = em.reject_contact(vendor_id, candidate_id)
        return out(cand)

    @app.post("/api/plans/<plan_id>/shortlist/test-vendor")
    def shortlist_test_vendor(plan_id):
        """A vendor whose address is yours, for testing. It needs your statement that you own the address and is labelled TEST VENDOR everywhere."""
        plan_id, b = pid(plan_id), body(TestVendorBody)
        with lock:
            plan = em._scope(plan_id)
            biz, _ = em.add_test_vendor(b.name, b.city or plan.request.city, b.category, b.email, b.owns_address, b.by, plan_id)
            item = next(i for i in em.shortlist_view(plan_id) if i.business.vendor_id == biz.vendor_id)
        return out(item, 201)

    @app.post("/api/plans/<plan_id>/shortlist/user-vendor")
    def shortlist_user_vendor(plan_id):
        """A business you know, added by you with the contact you typed. Nothing is sent to it until you approve each message."""
        plan_id, b = pid(plan_id), body(UserVendorBody)
        with lock:
            biz, _ = em.add_user_vendor(b.name, b.city or "", b.category, b.email, b.whatsapp, b.by, plan_id)
            item = next(i for i in em.shortlist_view(plan_id) if i.business.vendor_id == biz.vendor_id)
        return out(item, 201)

    @app.post("/api/plans/<plan_id>/replies/paste")
    def reply_paste(plan_id):
        """A reply you got outside the app, pasted in. It is read and confirmed line by line like any other, and is never shared with anyone."""
        plan_id, b = pid(plan_id), body(PasteReplyBody)
        with lock:
            msg = em.paste_reply(plan_id, b.vendor_id, b.text, b.by, b.channel)
        return out(msg, 201)

    @app.get("/api/vendors/directory")
    def vendor_directory():
        with lock:
            return jsonify(em.vendor_directory())

    @app.get("/api/vendors/labels")
    def vendor_labels():
        with lock:
            return jsonify(em.vendor_labels())

    @app.get("/api/suppressions")
    def suppressions_get():
        """The do-not-contact list: one entry per address, covering every plan and job."""
        with lock:
            items = em.suppression_list()
        return jsonify([{**json.loads(s.model_dump_json()), "active": s.active, "note": note} for s, note in items])

    @app.post("/api/suppressions")
    def suppressions_add():
        b = body(SuppressBody)
        with lock:
            s = em.suppress_address(b.channel, b.value, b.by, b.note)
        return out(s, 201)

    @app.post("/api/suppressions/<suppression_id>/lift")
    def suppressions_lift(suppression_id):
        """Only you can lift one, and the record says who and why."""
        suppression_id, b = pid(suppression_id), body(LiftBody)
        with lock:
            s = em.lift_suppression(suppression_id, b.by, b.reason, b.acknowledge_reason)
        return out(s)

    @app.get("/api/unmatched")
    def unmatched_get():
        """Stop-like mail and delivery failures that are not tied to anything we sent. They change nothing until you decide."""
        with lock:
            items = em.unmatched_messages()
        return jsonify([json.loads(m.model_dump_json()) for m in items])

    @app.post("/api/unmatched/<message_id>/dismiss")
    def unmatched_dismiss(message_id):
        message_id = pid(message_id)
        body(EmptyBody)
        with lock:
            m = em.dismiss_unmatched(message_id)
        return out(m)

    @app.post("/api/unmatched/<message_id>/suppress")
    def unmatched_suppress(message_id):
        message_id, b = pid(message_id), body(ByBody)
        with lock:
            s = em.suppress_unmatched(message_id, b.by)
        return out(s, 201)

    @app.get("/api/reply-reviews")
    def reply_reviews_get():
        """Replies from an address we wrote to for more than one event, with nothing in them saying which. Nothing is filed, read or priced until you pick the event."""
        with lock:
            items = em.reply_reviews_open()
        return jsonify([json.loads(r.model_dump_json()) for r in items])

    @app.post("/api/reply-reviews/<review_id>/assign")
    def reply_review_assign(review_id):
        review_id, b = pid(review_id), body(AssignReplyBody)
        with lock:
            msg = em.assign_reply(review_id, b.plan_id)
        return out(msg, 201)

    @app.post("/api/reply-reviews/<review_id>/dismiss")
    def reply_review_dismiss(review_id):
        review_id = pid(review_id)
        body(EmptyBody)
        with lock:
            r = em.dismiss_reply_review(review_id)
        return out(r)

    @app.get("/api/community/profiles")
    def community_profiles():
        """The community subtypes (data in community_profiles.json) for the intake review card."""
        from eventmate.planning.community import load_profiles

        p = load_profiles()
        return jsonify({"default": p.default, "version": p.version, "reviewed": p.reviewed, "profiles": [{"key": k, "label": v.label} for k, v in p.profiles.items()]})

    @app.get("/api/plans/<plan_id>/community")
    def community_get(plan_id):
        plan_id = pid(plan_id)
        with lock:
            status = em.community_status(plan_id, today_fn())
            details = em.store.latest(plan_id).request.community
        return jsonify({**json.loads(status.model_dump_json()), "details": json.loads(details.model_dump_json()) if details else None,
                        "suggested_roles": [{"role": r, "note": n} for r, n in status.suggested_roles]})

    @app.post("/api/plans/<plan_id>/community")
    def community_update(plan_id):
        """Replace the community details as a validated new version, or refuse with repairs. Nothing is saved on a refusal."""
        plan_id, details = pid(plan_id), body(CommunityDetails)
        with lock:
            res = em.update_community(plan_id, details, today_fn())
        return out(res, 200 if res.status == "replanned" else (409 if res.status == "needs_decision" else 422))

    @app.post("/api/plans/<plan_id>/community/items/<item_id>")
    def community_item(plan_id, item_id):
        """The user's mark on one conditional requirement. to;doh-the event planner never marks one itself."""
        plan_id, b = pid(plan_id), body(CommunityItemBody)
        if not re.fullmatch(r"[a-z0-9_]{1,40}", item_id):
            raise ValueError("invalid item id")
        with lock:
            rec = em.set_community_item(plan_id, item_id, b.status, b.by, b.on, b.note, today_fn())
        return out(rec, 201)

    @app.post("/api/venues/<venue_id>/accessibility")
    def venue_accessibility(venue_id):
        """What a venue told the user about its access, with evidence. It turns an unverified item into a pass or a failure on the next check."""
        venue_id, b = pid(venue_id), body(AccessBody)
        with lock:
            rec = em.record_venue_accessibility(venue_id, tuple(b.features), b.evidence_ref, b.entered_by)
        return out(rec, 201)

    @app.get("/api/contacts")
    def contacts_get():
        with lock:
            items = em.vendor_contacts()
        return jsonify([json.loads(c.model_dump_json()) for c in items])

    @app.post("/api/contacts")
    def contacts_set():
        b = body(ContactBody)
        with lock:
            c = em.set_vendor_contact(b.vendor_id, b.email, b.whatsapp, b.preferred)
        return out(c, 201)

    @app.post("/api/plans/<plan_id>/outreach/<draft_id>/sent")
    def outreach_sent(plan_id, draft_id):
        """The user says they sent it. to;doh-the event planner cannot verify that."""
        plan_id, draft_id = pid(plan_id), pid(draft_id)
        body(EmptyBody)
        with lock:
            draft = em.mark_outreach_sent(plan_id, draft_id)
        return out(draft)
