"""Find businesses for a plan and category and put them on its shortlist.

Sources are explicit about what they cover. OpenStreetMap is queried for venues only: there is no verified OSM tag mapping here for caterers or
photographers, so asking for them says "not supported by this source" instead of returning an empty list that looks like "none exist". Google Places
covers every category but needs an API key. A found business becomes an ``ExternalBusiness``; it is never written to the vendor database.
"""

from __future__ import annotations

import re

from eventmate.integrations.discovery import OverpassSource
from eventmate.integrations.places import PlacesSource
from eventmate.schemas import PlanVersion, SearchOutcome, VendorCategory
from eventmate.sourcing.contacts import FLAG_GENERIC, FREE_MAIL
from eventmate.sourcing.externals import ExternalStore
from eventmate.sourcing.shortlist import ShortlistStore

SOURCES = ("osm", "places")
OSM_KINDS = {VendorCategory.VENUE: "venue"}
_TAGS = re.compile(r"<[^>]*>")


# FUTURE(quotes-register): a discovered place becomes an ExternalBusiness only when "Find vendors I can email" is pressed, so "earlier quotes" cannot show beside a listing before that. Register each place (once, by its place id) when its listing is shown.
# (quotes-carry, done in LM2) requests and replies are filed under brief_<id>; when the brief becomes a plan the link is recorded (planning/brief_links.py) and both ids read the same files; a pending brief with a request unanswered is not swept.
# (quotes-menus, done in WP3: see CHANGELOG)
# FUTURE(quotes-voice): many vendors answer with a voice note or a call. Transcribe a voice note (the app has audio code) into the same reading, shown as the vendor's words; a call is typed in by the user through the same parser.
# (quotes-attachments, done in WP3: see CHANGELOG)
# FUTURE(quotes-template-labels): the labels are matched by their exact text, so a label is only ever added, never reworded, or replies to old requests stop reading. Version the template if it must change.
# FUTURE(quotes-compare): a recommended caterer + venue + photographer combination for the budget (the ranking per category is in quote_compare.py).
# (quotes-silent, done in WP3: see CHANGELOG)
# FUTURE(quotes-simulator): a simulator that sends the user's own test addresses realistic replies (clear, messy, wrong, late). Nothing has been tested against a real mailbox yet (docs/EMAIL_SETUP.md).
# FUTURE(quotes-limits): a cap on requests per brief and per day, the suppression list wired into this flow, and a clear sender identity, before any real vendor is contacted.
# FUTURE(quotes-browser): some vendor sites show contacts or prices only after scripts run. Feed eventmate/browser (read-only: it clicks and submits nothing) into the contact finder.


class SourceNotSupported(ValueError):
    """The chosen source does not cover this category. Not an empty result: a statement that this source cannot answer."""


def clean_name(text: str) -> str:
    text = _TAGS.sub(" ", text or "")
    return " ".join("".join(c for c in text if c.isprintable() or c == " ").split())[:120]


class VendorSearch:
    def __init__(self, externals: ExternalStore, shortlist: ShortlistStore, discovery: OverpassSource, places: PlacesSource):
        self.externals, self.shortlist, self.discovery, self.places = externals, shortlist, discovery, places

    def default_source(self, category: VendorCategory) -> str:
        return "osm" if category in OSM_KINDS else "places"

    def search(self, plan: PlanVersion, category: VendorCategory, source: str | None = None, limit: int = 10) -> SearchOutcome:
        source = source or self.default_source(category)
        if source not in SOURCES:
            raise ValueError(f"source must be one of {', '.join(SOURCES)}")
        if not 1 <= limit <= 20:
            raise ValueError("limit must be 1-20")
        city = plan.request.city
        if source == "osm":
            if category not in OSM_KINDS:
                raise SourceNotSupported(
                    f"{category.value} is not supported by OpenStreetMap search here (it is queried for venues only; there is no verified tag mapping for {category.value}s). "
                    "Use Google Places for this category."
                )
            return self._osm(plan, category, city, limit)
        return self._places(plan, category, city, limit)

    def _osm(self, plan: PlanVersion, category: VendorCategory, city: str, limit: int) -> SearchOutcome:
        result = self.discovery.venues(city, OSM_KINDS[category], limit)
        added = 0
        for c in result.candidates:
            name = clean_name(c.name)
            if not name:
                continue
            url = f"https://www.openstreetmap.org/{c.osm_ref}"
            before = self.externals.get(self._vid(f"osm:{c.osm_ref}"))
            biz = self.externals.add(f"osm:{c.osm_ref}", name, city, category, "osm", source_url=url, website=c.website)
            self.shortlist.add_entry(plan.plan_id, category, biz.vendor_id)
            if c.email:  # the listing's own field: a candidate with its source, never a contact until the user confirms it
                value = c.email.strip().casefold()
                flags = (FLAG_GENERIC,) if value.rsplit("@", 1)[-1] in FREE_MAIL else ()
                self.shortlist.add_candidates(biz.vendor_id, [{"channel": "email", "value": value, "source_url": url, "source_kind": "listing_field", "flags": flags}])
            added += before is None
        return SearchOutcome(source="osm", category=category, city=city, added=added, total=len(self.shortlist.entries(plan.plan_id, category)), warnings=tuple(result.warnings))

    def _places(self, plan: PlanVersion, category: VendorCategory, city: str, limit: int) -> SearchOutcome:
        result = self.places.search(category.value, city, limit=limit)  # the listing's own fields are not asked for here: nothing from them is kept, and a website is fetched when a contact search is asked for
        added, skipped = 0, 0
        warnings: list[str] = list(result.warnings)
        for p in result.places:
            if not p.place_id:
                skipped += 1
                continue
            if not clean_name(p.name):
                continue
            ref = f"gplaces:{p.place_id}"
            before = self.externals.get(self._vid(ref))
            biz = self.externals.add(ref, "", city, category, "gplaces")  # only the place id is kept; the listing's name, link, website and rating are fetched again when shown
            self.shortlist.add_entry(plan.plan_id, category, biz.vendor_id)
            added += before is None
        if skipped:
            warnings.append(f"{skipped} place(s) without a place id were skipped")
        return SearchOutcome(source="places", category=category, city=city, added=added, total=len(self.shortlist.entries(plan.plan_id, category)), warnings=tuple(warnings))

    @staticmethod
    def _vid(external_id: str) -> str:
        from eventmate.schemas import external_vendor_id

        return external_vendor_id(external_id)
