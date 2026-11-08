"""to;doh-the event planner facade: wires every agent and exposes the whole workflow.

    em = EventMate()
    outcome = em.plan("Plan a 120-person engagement party in Pune ...")
    em.confirm(plan_id, VendorCategory.VENUE)        # the user confirmed a booking
    em.replan(plan_id, Disruption(kind=..., ...))    # new immutable version + diff
    em.explain(plan_id)                              # readable text with reasons
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import date, timedelta
from pathlib import Path
from collections.abc import Callable

from eventmate.guestlist import GuestListStore, GuestPreview, check_against, parse_guest_csv, totals_of
from eventmate.voice.notes import VoiceNoteStore
from eventmate import migrations
from eventmate.integrity import Guard
from eventmate.retention import PlanBusy, delete_plan_files, purge_old_logs, report, scrub_plan_from_logs, sweep_stale_temp, write_tombstone
from eventmate.agents.budget import BudgetAgent, BudgetPolicy
from eventmate.agents.explainer import ExplainerAgent, Explanation
from eventmate.agents.guest import GuestAgent, GuestRepository
from eventmate.agents.manager import ManagerAgent
from eventmate.agents.planner import PlannerAgent
from eventmate.agents.replanner import ReplannerAgent, diff_plans
from eventmate.agents.scheduler import SchedulingAgent
from eventmate.agents.tradeoff import TradeoffAgent
from eventmate.agents.validator import ValidatorAgent
from eventmate.agents.vendor import VendorAgent, VendorRepository
from eventmate.integrations.leads import LeadStore
from eventmate.planning import community as community_mod
from eventmate.planning.community import AccessibilityStore, ChecklistStore
from eventmate.messaging.digests import DigestStore
from eventmate.messaging.notify import OwnerNotifier
from eventmate.planning.ideas import IdeaService
from eventmate.messaging.channels import Channel, channels_from_env
from eventmate.messaging.reply_review import ReplyReviewStore
from eventmate.messaging.conversations import LOOKBACK, ConversationService, HeldMessages
from eventmate.messaging.outreach import OutreachStore, normalise_phone
from eventmate.followthrough.worker import JobWorker
from eventmate.followthrough.jobs import JobStore
from eventmate.sourcing.contacts import find_contacts
from eventmate.sourcing.externals import ExternalStore, ResolvedVendor, clean_email, slug
from eventmate.evidence.ledger import EvidenceLedger, FactState
from eventmate.followthrough.watches import WatchRunner, WatchStore
from eventmate.planning import brief_scope, plan_graph
from eventmate.planning.brief_links import BriefLinks
from eventmate.sourcing.contact_log import ContactLogStore
from eventmate.integrations.health import HEALTH, Watched
from eventmate.payments.approvals import ProposalStore
from eventmate.payments.desk import PaymentDesk
from eventmate.payments.providers import provider_from_env
from eventmate.payments.terms import TermsStore
from eventmate.planning.staging import StageClosed, StageGone, StagingStore, StagedIntake
from eventmate.messaging import outreach as outreach_mod
from eventmate.sourcing import cards, quote_compare, quote_template
from eventmate.pricing import shared_quotes
from eventmate.pricing.shared_quotes import SharedQuoteStore
from eventmate.sourcing.quote_records import QuotedProviderStore, to_provider
from eventmate.sourcing.quote_readings import AttachedQuote, DocLine, QuoteAssessment, QuoteConfirmationStore, assess, parse_typed
from eventmate.sourcing.cards import VendorCard
from eventmate.sourcing.search import VendorSearch, clean_name
from eventmate.sourcing.suppression import NeedsAcknowledgement, SuppressionStore, UnmatchedStore, domain_note
from eventmate.sourcing.shortlist import ShortlistStore
from eventmate.pricing.ledger import Ledger
from eventmate.pricing.observations import ObservationStore, old_reasons
from eventmate.pricing.vendorscore import score_category
from eventmate.planning import intake
from eventmate.llm import FallbackClient, LLMClient, client_from_env
from eventmate.logs import RunLogs
from eventmate.planning.rules import Advisory, advisories_for
from eventmate.plan_store import PlanStore, draft_from_plan
from eventmate.schemas.integrations import MAX_DATE_RANGE_DAYS
from eventmate.schemas import HiddenCost, InboundMessage
from eventmate.followthrough.worker import CONFIRM_CONFIDENCE
from eventmate.schemas import CheapestDates, GuestTravel, ConstraintViolation, ObservationSource, AgentName, DecisionType, ContactFindResult, ExternalBusiness, SearchOutcome, ShortlistItem, VendorContact, VendorRef, WeatherAdvice, BookingStatus, DecisionLogEntry, SelectionQuoteStatus, LedgerEntry, LedgerSummary, Disruption, DisruptionKind, PlanDiff, PlanOutcome, PlanVersion, ReplanOutcome, TradeoffOption, TradeoffReport, ValidationReport, VendorCategory
from eventmate.symbolic import SymbolicChecker
from eventmate.integrations.deals import DealAgent
from eventmate.integrations.discovery import OverpassSource
from eventmate.integrations.web_access import SafeFetcher
from eventmate.integrations.websearch import default_provider
from eventmate.env import load_env
from eventmate.integrations.places import PlacesSource
from eventmate.integrations.weather import OpenWeatherSource
from eventmate.integrations.weather_advice import advise
from eventmate.browser.render import RenderingFetcher
from eventmate.integrations.travel import FlightProvider, FliProvider, TravelError, check_range, check_search, search_multi_city
from eventmate.integrations.tripsearch import search_trip

__all__ = ["EventMate", "HeldMessages"]  # HeldMessages is re-exported: callers import it from here


class _NoClients:
    clients: list = []


class EventMate:
    def __init__(
        self,
        log_dir: Path | None = None,
        plan_dir: Path | None = None,
        run_id: str | None = None,
        repo: VendorRepository | None = None,
        guest_repo: GuestRepository | None = None,
        symbolic: SymbolicChecker | None = None,
        llm: LLMClient | None = None,
        require_symbolic: bool = True,
        store_llm_text: bool = False,
        flights: FlightProvider | None = None,
        discovery: OverpassSource | None = None,
        weather: OpenWeatherSource | None = None,
        places: PlacesSource | None = None,
        channels: dict[str, Channel] | None = None,
        shared_dir: Path | None = None,
    ):
        self.env = load_env()  # secrets from .env (names only are kept); values already in the environment win
        self._today = date.today()
        self.logs = RunLogs.create(log_dir, run_id, store_llm_text)
        self.repo = repo or VendorRepository()
        self.store = PlanStore(plan_dir)
        self.ledger = Ledger(self.store.root)
        self.payments = PaymentDesk(TermsStore(self.store.root, lambda: self.outreach_store.now()), ProposalStore(self.store.root, lambda: self.outreach_store.now()), lambda: provider_from_env(os.environ), self.ledger_add,
                                    lambda: self.outreach_store.now().date(),
                                    external_allowed=shared_dir is None)  # money terms, approvals and instructions (payments/desk.py); never moves money from a hosted visitor's workspace
        self.lead_store = LeadStore(self.store.root)
        self.flights = Watched(flights or FliProvider(), "flights", ("search_one_way", "cheapest_dates"))  # each records how the service is doing (integrations/health.py)
        self.discovery = Watched(discovery or OverpassSource(), "overpass", ("venues",))
        self.weather = Watched(weather or OpenWeatherSource(), "weather", ("forecast",))
        self.places = Watched(places or PlacesSource(), "places", ("search", "details"))
        self.router_clients = lambda: (client_from_env(tier="light") or _NoClients()).clients  # light tier: OpenRouter free first, NVIDIA behind
        self.deals_factory = lambda on_call: DealAgent(default_provider(), RenderingFetcher(), on_call=on_call)  # static pages first; the read-only browser only for a page with almost no text
        self.observations = ObservationStore(self.store.root, self.repo)
        self.repo.quotes = self.observations
        self.evidence = EvidenceLedger(self.store.root)
        self.watches = WatchStore(self.store.root)  # makes no file until the first watch
        self.last_watch_report: dict = {}
        self.externals = ExternalStore(self.store.root, evidence=self.evidence)
        self.externals.scrub_google_content()  # an earlier version kept Google listing content; only the place id may be kept
        self.quoted = QuotedProviderStore(self.store.root)  # plannable records made from fully confirmed quotes
        self.repo.quoted = self.quoted.providers
        self.shared_quotes = SharedQuoteStore(shared_dir) if shared_dir is not None and shared_quotes.enabled() else None  # what other people were quoted by the same vendor (pricing/shared_quotes.py): shown, never priced from
        self.quote_confirmations = QuoteConfirmationStore(self.store.root)  # what the user confirmed, field by field, from a vendor's reply
        self.staging = StagingStore(self.store.root)  # briefs waiting for the user's approval; nothing is planned from one until it is committed
        self.shortlist = ShortlistStore(self.store.root, evidence=self.evidence)
        self.brief_links = BriefLinks(self.store.root)  # which plan a brief became: its requests and replies follow it (planning/brief_links.py)
        self.shortlist.home = self.quote_confirmations.home = self.brief_links.home
        self.contact_log = ContactLogStore(self.store.root)
        self.contact_log.home = self.brief_links.home
        self.observations.external_lookup = self.externals.get
        self.suppressions = SuppressionStore(self.store.root)
        self.unmatched = UnmatchedStore(self.store.root)
        self.vendor_search = VendorSearch(self.externals, self.shortlist, self.discovery, self.places)
        self.contact_fetcher: Callable[[], object] = SafeFetcher  # tests replace this; the real one has the SSRF, robots and size rules
        self.access_store = AccessibilityStore(self.store.root)
        self.checklist_store = ChecklistStore(self.store.root)
        self.guest_lists = GuestListStore(self.store.root)
        self.voice_notes = VoiceNoteStore(self.store.root)
        self.repo.access = self.access_store
        self.outreach_store = OutreachStore(self.store.root, self.observations.checklists)
        self.outreach_store.home = self.brief_links.home
        self.staging.protect = self._brief_awaits_replies
        self._jobs: JobWorker | None = None
        self.jobs_clock = None  # tests inject a clock here before the worker is first used
        self._job_store: JobStore | None = None
        self.digest_store = DigestStore(self.store.root)
        self.conversations = ConversationService(self.outreach_store, channels or channels_from_env(os.environ), self._scope)
        self.integrity = Guard(self.store.root)  # while an authoritative record file is damaged, nothing is sent or approved
        self.conversations.health = self.integrity.blocker
        self.conversations.guards.append(self._job_guard)
        self.conversations.suppressions, self.conversations.unmatched = self.suppressions, self.unmatched
        self.reply_reviews = ReplyReviewStore(self.store.root, now=lambda: self.outreach_store.now())
        self.conversations.reviews, self.conversations.event_key = self.reply_reviews, self.brief_links.event_key
        self.conversations.on_reply = self._reply_landed
        if os.environ.get("EVENTMATE_READ_QUOTE_ATTACHMENTS") == "1":  # off unless the user turns it on: an attachment is a stranger's file
            self.conversations.read_attachment = self._read_attachment
        self.notifier = OwnerNotifier(
            self.store.root, self.digest_store, lambda: self.conversations.channels["email"], self._jobs_of, self._vendor_names, self._vendor_addresses,
            self._own_addresses,
            lambda: self.outreach_store.killed or self.outreach_store.paused,  # owner emails are messages: the pause holds them too
        )
        decisions = self.logs.decisions
        scheduler = SchedulingAgent(decisions)
        budget_policy = BudgetPolicy()
        budget = BudgetAgent(decisions, budget_policy)
        vendor = VendorAgent(self.repo, decisions)
        guest = GuestAgent(guest_repo or GuestRepository(), decisions)
        planner = PlannerAgent(self.logs, scheduler, symbolic or SymbolicChecker())
        self.validator = ValidatorAgent(self.repo, self.logs, scheduler.policy, require_symbolic, budget_policy)
        self.llm = llm
        self._intake_light = None
        self.ideas = IdeaService(self.store.root, self.logs, self.repo, llm)
        self.explainer = ExplainerAgent(self.logs, llm)
        self.manager = ManagerAgent(self.repo, decisions, budget, vendor, guest, planner, self.validator, self.explainer, self.store)
        self.tradeoff = TradeoffAgent(self.repo, decisions, self._dry_run_ok, budget_policy)
        self._guest_list_id: str | None = None
        with contextlib.suppress(Exception):  # approve and dispatch re-check every draft anyway; a store that cannot be read yet must not stop start-up
            self.conversations.supersede_all_stale()  # drafts that predate version tracking, or that no longer match their plan, are marked stale once
        self.replanner = ReplannerAgent(self.logs, self.repo, budget, vendor, guest, planner, self.validator, self.store)
        sweep_stale_temp(self.store.root, self.logs.decisions.log_dir)  # what a crash left half-written, once it is old enough that nothing is still writing it
        from eventmate.transfer import recover_interrupted_imports

        recover_interrupted_imports(self)  # an import whose process died part-way is rolled back, so a plan never exists without its ledger and guest list

    def _clock(self, today: date | None) -> None:
        """Quote expiry is judged against the caller's 'today' (tests and the web app inject one)."""
        if today is not None:
            self.repo.clock = lambda: today

    def plan(self, text: str, today: date | None = None, guest_list_id: str | None = None) -> PlanOutcome:
        self._clock(today)
        return self.manager.run(text, today=today, guest_list_id=guest_list_id)

    def intake_turn(self, messages, prior: intake.IntakeDraft | None = None, today: date | None = None, expecting: str | None = None, answer: intake.IntakeAnswer | None = None, staged_id: str | None = None) -> intake.IntakeTurn:
        """Propose a draft from the organiser's words and stage it, pending the user's approval. Nothing is planned until ``commit_staged``.

        ``expecting`` is the fact the last question was about; ``answer`` is an option the user pressed (see ``intake.propose``). With a ``staged_id`` the staged draft is the one corrected, not
        whatever the caller sends back; a staged brief that is gone (timed out) falls back to the caller's own draft and is staged again."""
        today = today or date.today()
        staged = None
        if staged_id:
            try:
                staged = self.staging.require(staged_id)
            except StageGone:
                staged = None
            if staged is not None and staged.state == "expired":
                staged = None
            elif staged is not None and staged.state != "pending_validation":
                raise StageClosed("this brief is already being built, so it can no longer be changed")
            elif staged is not None:
                prior = staged.draft
        turn = intake.propose(messages, prior, today, self.repo.cities(), self.logs, self._intake_model(), expecting=expecting, answer=answer, model_when=os.environ.get("EVENTMATE_INTAKE_MODEL", "auto"))
        history = (*(staged.messages if staged else ()), *([] if answer is not None else list(messages)[-1:]))  # only the newest message is read; a pressed option is not a message
        row = self.staging.overwrite(staged.staged_id, turn.draft, history, turn.source, staged.version) if staged else self.staging.create(turn.draft, history, turn.source)
        return self._staged_turn(turn, row)

    @staticmethod
    def _staged_turn(turn: intake.IntakeTurn, row: StagedIntake) -> intake.IntakeTurn:
        return turn.model_copy(update={"staged_id": row.staged_id, "version": row.version, "draft_hash": row.draft_hash, "state": row.state, "expires_at": row.expires_at})

    def edit_staged(self, staged_id: str, patch: dict, version: int, today: date | None = None) -> intake.IntakeTurn:
        """The user typed new values onto the review card. They pass the same schema as a reader's draft, replace the staged one, and answer any open question about them."""
        today = today or date.today()
        cur = self.staging.require(staged_id)
        if cur.state == "expired":
            raise StageGone("that brief timed out; start it again")
        if cur.state != "pending_validation":
            raise StageClosed("this brief is already being built, so it can no longer be changed")
        turn = intake.edited(cur.draft, patch, self.repo.cities(), today)
        row = self.staging.overwrite(staged_id, turn.draft, cur.messages, cur.source, version)
        return self._staged_turn(turn, row)

    def staged_turn(self, staged_id: str, today: date | None = None) -> intake.IntakeTurn:
        """The staged brief as the review card shows it, read again from what is stored (a reloaded page, another device)."""
        cur = self.staging.require(staged_id)
        turn = intake.edited_view(cur.draft, self.repo.cities(), cur.source)
        return self._staged_turn(turn, cur)

    def commit_staged(self, staged_id: str, version: int, digest: str, today: date | None = None, quoted_only: bool = False) -> tuple[StagedIntake, bool]:
        """The user's "done". Refused unless the brief is ready, buildable and exactly the one they reviewed; then pending becomes processing, once.
        Returns the row and whether this call started the build (a repeat does not start a second one)."""
        today = today or date.today()
        cur = self.staging.require(staged_id)
        if cur.state == "pending_validation":
            with self._vendors_visible(quoted_only):
                known = {c.lower() for c in self.repo.cities()}
            if cur.draft.city and cur.draft.city.lower() not in known:
                raise intake.IntakeError(f"I have no vendor records for {cur.draft.city} yet, so a plan there cannot be built" if not quoted_only else f"no vendor in {cur.draft.city} has a confirmed and chosen quote yet: ask for quotes, confirm them, and choose one under Compare")
            intake.to_request(cur.draft, "\n".join(cur.messages)[:2000], today, cur.source, list(cur.messages))  # raises IntakeError for anything missing, open or skipped
        return self.staging.begin_commit(staged_id, version, digest)

    def reopen_staged(self, staged_id: str, today: date | None = None) -> intake.IntakeTurn:
        """A failed build goes back to pending so the user can correct the brief and press done again."""
        return self.staged_turn(self.staging.reopen(staged_id).staged_id, today)

    def _vendors_visible(self, quoted_only: bool):
        return self.repo.quoted_only() if quoted_only else contextlib.nullcontext()

    def build_staged(self, staged_id: str, today: date, guest_list_id: str | None = None, quoted_only: bool = False) -> StagedIntake:
        """The build itself, run by the commit queue. It records its outcome on the staged brief, whatever happens."""
        row = self.staging.require(staged_id)
        try:
            with self._vendors_visible(quoted_only):
                outcome = self.confirm_intake(row.draft, "\n".join(row.messages)[:2000], today, guest_list_id, row.source, list(row.messages))
        except ValueError as exc:  # an IntakeError or a refusal with a message written for the user
            return self.staging.finish(staged_id, False, error=str(exc)[:300])
        except Exception:
            logging.getLogger(__name__).exception("staged build %s failed", staged_id)  # the user gets a plain sentence; the cause is in the application log
            return self.staging.finish(staged_id, False, error="The plan could not be built. Nothing was stored; try again.")
        result = {
            "status": outcome.status, "plan_id": outcome.plan.plan_id if outcome.plan else None, "violations": [v.model_dump() for v in outcome.violations],
            "tasks": [{"id": t.id, "agent": t.agent.value, "status": t.status.value, "depends_on": list(t.depends_on)} for t in outcome.task_graph.tasks],
            "reality": json.loads(outcome.intake.reality.model_dump_json()) if outcome.intake.reality else None,
            "graph": plan_graph.build(outcome.plan) if outcome.plan else None,  # the finished plan as nodes and edges, in the same payload the page follows
        }
        if outcome.plan is not None:
            self.brief_links.link(staged_id, outcome.plan.plan_id)  # what was asked and answered under the brief now belongs to the plan too
        return self.staging.finish(staged_id, outcome.plan is not None, result, "" if outcome.plan else "The plan could not pass the hard checks, so nothing was stored.")

    def _intake_model(self):
        """The model that reads a brief. Normally the app's own; ``EVENTMATE_INTAKE_TIER=light`` gives it the light tier (a small model, a few seconds when its provider is up). Not the default: a
        light model that cannot be reached costs its whole timeout on every message, and on the machine this was measured on the heavy one was the one that answered. A model handed in (a test's fake) is used as it is."""
        if os.environ.get("EVENTMATE_INTAKE_TIER") != "light" or not isinstance(self.llm, FallbackClient):
            return self.llm
        if self._intake_light is None:
            self._intake_light = client_from_env(tier="light") or self.llm
        return self._intake_light

    def confirm_intake(self, draft: intake.IntakeDraft, raw_text: str, today: date | None = None, guest_list_id: str | None = None, source: str = "rule:parser", messages=None) -> PlanOutcome:
        """The user confirmed the review card: run the full pipeline on exactly what they approved."""
        today = today or date.today()
        self._clock(today)
        request = intake.to_request(draft, raw_text, today, source, messages)
        return self.manager.run_request(request, today=today, guest_list_id=guest_list_id, change_reason="initial plan from the intake conversation, confirmed by the user")

    def _dry_run_ok(self, request):
        """(a full plan was built, what that plan would leave undecided). Used by the trade-off negotiator."""
        out = self.manager.run_request(request, today=self._today, guest_list_id=self._guest_list_id, persist=False)
        if out.status != "planned":
            return False, ()
        return True, tuple(u.label or u.name for u in out.plan.validation.blocking_unverified())

    def verification(self, plan_id: str, version: int | None = None) -> dict:
        """Where a plan stands right now: "verified" or "needs_verification". Community plans are re-checked against today's data, so a venue's answer recorded after the plan was made counts at once."""
        plan = self.store.load(plan_id, version)
        report = self.revalidate(plan_id, version) if plan.request.event_type.value == "community" else plan.validation
        blocking = report.blocking_unverified()
        return {
            "state": "verified" if report.fully_verified else ("needs_verification" if report.passed else "failed"),
            "label": report.verification_label(),
            "items": [{"name": u.name, "label": u.label or u.name, "detail": u.detail, "repair": u.repair} for u in blocking],
            "other_unverified": [{"name": u.name, "label": u.label or u.name, "detail": u.detail} for u in report.unverified if not u.blocks_verified],
        }

    def tradeoffs(self, text: str, today: date | None = None, guest_list_id: str | None = None, travel: GuestTravel | None = None, fares: CheapestDates | None = None) -> TradeoffReport:
        """Ranked, verified ways to make a failing request plannable. Nothing is stored or applied.

        ``travel`` and ``fares`` (from ``guest_fares``) only add flight notes to date-shift options; they never change ranking or what is verified.
        """
        self._today, self._guest_list_id = today or date.today(), guest_list_id
        self._clock(self._today)
        return self.tradeoff.propose(self.manager.parse(text, self._today), self._today, fares, travel)

    def tradeoffs_for_draft(self, draft: intake.IntakeDraft, raw_text: str, today: date | None = None, guest_list_id: str | None = None, travel: GuestTravel | None = None, fares: CheapestDates | None = None, messages=None) -> TradeoffReport:
        """The same as ``tradeoffs``, starting from the draft the user confirmed instead of re-reading what they typed, so an option they pressed (a unit, a city, a guest count) is kept.
        The draft passes the same check as a confirm: nothing open, nothing skipped."""
        self._today, self._guest_list_id = today or date.today(), guest_list_id
        self._clock(self._today)
        request = intake.to_request(draft, raw_text, self._today, messages=messages)
        return self.tradeoff.propose(request, self._today, fares, travel)

    def guest_fares(self, travel: GuestTravel, today: date | None = None) -> CheapestDates | None:
        """The guests' cheapest fares for the next 61 days, or None when the flight tool fails (the trade-offs are then shown without fares)."""
        today = today or date.today()
        try:
            return self.cheapest_flight_dates(travel.origin, travel.destination, today + timedelta(days=1), today + timedelta(days=MAX_DATE_RANGE_DAYS), today=today)
        except (TravelError, ValueError):
            return None

    def apply_tradeoff(self, option: TradeoffOption, today: date | None = None, guest_list_id: str | None = None) -> PlanOutcome:
        """The user chose this option: run it through the full pipeline and store the resulting plan."""
        self._clock(today)
        return self.manager.run_request(
            option.request, today=today, guest_list_id=guest_list_id, change_reason=f"trade-off chosen by the user: {option.title}"[:500]
        )

    def advisories(self, plan_id: str) -> list[Advisory]:
        """Advisory checklists for the plan's dietary requirements. Never constraints."""
        return advisories_for(self.store.latest(plan_id).request)

    # ---- imported guest lists: names stay in one private file per plan; everything else sees counts

    def guest_preview(self, plan_id: str, raw) -> GuestPreview:
        """Parse an uploaded CSV without saving it. The token is single use and only issued for a file with no row problems."""
        plan = self.store.latest(plan_id)
        result = parse_guest_csv(raw)
        token = None if (result.errors or not result.guests) else self.guest_lists.preview(plan_id, result)
        return GuestPreview(guests=result.guests, row_errors=result.errors, totals=result.totals, check=check_against(result.totals, plan.request.guest_count), can_commit=token is not None, preview_token=token)

    def guest_commit(self, plan_id: str, token: str) -> dict:
        plan = self.store.latest(plan_id)
        guests = self.guest_lists.commit(plan_id, token)
        totals = totals_of(guests)
        self._log_guest_list(plan_id, f"Guest list saved for plan {plan_id}: {totals.rows} row(s), {totals.heads} people")
        return {"totals": json.loads(totals.model_dump_json()), "check": json.loads(check_against(totals, plan.request.guest_count).model_dump_json())}

    def guest_summary(self, plan_id: str) -> dict | None:
        """Totals and the check against the stated headcount for the saved list, or None. Never names."""
        plan = self.store.latest(plan_id)
        guests = self.guest_lists.load(plan_id)
        if guests is None:
            return None
        totals = totals_of(guests)
        return {"totals": json.loads(totals.model_dump_json()), "check": json.loads(check_against(totals, plan.request.guest_count).model_dump_json())}

    def guest_delete(self, plan_id: str) -> bool:
        self.store.latest(plan_id)
        gone = self.guest_lists.delete(plan_id)
        if gone:
            self._log_guest_list(plan_id, f"Guest list deleted for plan {plan_id}")
        return gone

    def _log_guest_list(self, plan_id: str, summary: str) -> None:
        """Counts only: a name or a raw cell must never reach a log."""
        self.logs.decisions.log(
            request_id="guest-list", agent=AgentName.GUEST, decision_type=DecisionType.ASSESSMENT, summary=summary,
            justification="the list is stored in one private file for this plan and shown to models only as counts", confidence=1.0, source="rule:guest_import",
        )

    def delete_plan(self, plan_id: str) -> dict:
        """Forget a plan: its versions, ledger, vendor messages, standing permission, jobs, ideas, digests, shortlist and checklist.
        Refused while a message for it is being sent. The do-not-contact lists and vendor price observations are not per plan and are kept."""
        self.store.latest(plan_id)  # unknown or hostile ids fail here, before anything is touched
        if any(d.status == "sending" for d in self.outreach_store.list(plan_id)):
            raise PlanBusy("a message for this plan is being sent; try again in a moment")
        self.guest_lists.delete(plan_id)  # also drops any preview still waiting for a commit
        files = delete_plan_files(self.store.root, plan_id) + self.voice_notes.delete_plan(plan_id)
        brief = self.brief_links.brief_of(plan_id)
        self.contact_log.forget_plan(plan_id, *([brief] if brief else []))
        if brief:  # the requests and replies were filed under the brief this plan was built from: they are this plan's, and go with it
            files += delete_plan_files(self.store.root, brief)
        jobs = self.jobs.store.delete_plan(plan_id) if (self.store.root / "jobs.db").exists() else 0
        # Order matters for a crash: everything that merely mentions the plan goes first, the versions go LAST. Until they are gone the plan still lists, so a delete that was
        # interrupted anywhere can simply be run again; if the versions went first, a crash would leave its id in the logs with no way left to delete it (found by crash-point testing).
        lines = scrub_plan_from_logs(self.logs.decisions.log_dir, plan_id)  # its questions, answers and decisions go with it; only the tombstone keeps the id
        self.store._forget_head(plan_id)
        write_tombstone(self.store.root / "tombstones.jsonl", "plan", plan_id)
        versions = self.store.delete(plan_id)
        self.logs.decisions.log(
            request_id="retention", agent=AgentName.MANAGER, decision_type=DecisionType.ASSESSMENT, summary="A plan was deleted at the user's request",
            justification=f"removed {versions} plan version(s), {files} message and ledger file(s), {jobs} job(s), {lines} log line(s); the tombstone records which plan and when", confidence=1.0, source="rule:retention",
        )
        return report(versions, files, jobs)

    def read_quote_document(self, vendor_id: str, filename: str, data: bytes) -> dict:
        """Read a vendor's quote file into priced lines for the user to confirm. The file is converted in a sandbox and is not kept; each line is noted as an
        unreviewed ('extracted') fact about the vendor, which no hard constraint may rest on until the user confirms it."""
        from eventmate.documents import quotes, sandbox

        if vendor_id not in self.vendor_directory():
            raise ValueError("unknown vendor")
        converted = sandbox.convert(data, filename)
        found = quotes.extract(converted.markdown)
        digest = hashlib.sha256(data).hexdigest()[:10]
        if "grand_total" in found.summary:  # the vendor's own total can be confirmed as the price too
            self.evidence.record(vendor_id, f"quote.{digest}.grand_total", {"label": "Grand total", "quantity": None, "unit_inr": None, "total_inr": found.summary["grand_total"], "basis": "total"},
                                 FactState.EXTRACTED, f"doc:{digest}")
        for n, ln in enumerate(found.lines, 1):
            self.evidence.record(vendor_id, f"quote.{digest}.l{n}", {"label": ln.label, "quantity": ln.quantity, "unit_inr": ln.unit_inr, "total_inr": ln.total_inr, "basis": ln.basis, "warnings": list(ln.warnings)},
                                 FactState.EXTRACTED, f"doc:{digest}")
        return {"document": digest, "engine": converted.engine, "truncated": converted.truncated, "summary": found.summary, "warnings": found.warnings,
                "lines": [{"key": str(n), "label": ln.label, "quantity": ln.quantity, "unit_inr": ln.unit_inr, "total_inr": ln.total_inr, "basis": ln.basis, "warnings": list(ln.warnings)} for n, ln in enumerate(found.lines, 1)]}

    def quote_documents(self, vendor_id: str) -> list[dict]:
        """Every quote file read for this vendor (uploaded or attached to a reply), newest first, with each line's state: still unreviewed, or one the user confirmed."""
        docs: dict[str, dict] = {}
        for r in self.evidence.records(vendor_id):
            m = re.fullmatch(r"quote\.([0-9a-f]{10})\.(l[1-9][0-9]{0,2}|grand_total)", r.field)
            if not m or r.source not in (f"doc:{m.group(1)}", "user_input") or not isinstance(r.value, dict):
                continue
            row = docs.setdefault(m.group(1), {"document": m.group(1), "lines": {}})["lines"].setdefault(m.group(2), {**r.value, "key": m.group(2).lstrip("l"), "confirmed": False})
            row["confirmed"] = row["confirmed"] or r.state is FactState.USER_CONFIRMED
        out = []
        for doc in reversed(list(docs.values())):
            lines = sorted((v for k, v in doc["lines"].items() if k != "grand_total"), key=lambda v: int(v["key"]))
            total = doc["lines"].get("grand_total")
            out.append({"document": doc["document"], "lines": lines, "summary": {"grand_total": total["total_inr"]} if total else {}, "grand_total_confirmed": bool(total and total["confirmed"]), "warnings": []})
        return out[:10]

    def price_effect(self, plan_id: str, vendor_id: str, document: str, line: str, today: date | None = None) -> dict:
        """What a confirmed quote line does to this plan: what the plan priced the vendor at, what the vendor now costs, and whether the line is only part of the quote
        (a quote observation replaces the vendor's whole price, so a single line of a longer quote would understate it). The plan itself is unchanged until it is re-priced."""
        row = next((r for r in self.quote_status(plan_id, today) if r.vendor_id == vendor_id), None)
        doc = next((d for d in self.quote_documents(vendor_id) if d["document"] == document), None)
        partial = bool(doc) and line != "grand_total" and (len(doc["lines"]) > 1 or bool(doc["summary"]))
        return {"in_plan": row is not None, "plan_cost": row.plan_cost if row else None, "now_costs": row.current_price if row else None, "price_stale": bool(row and row.price_stale),
                "covers_whole_quote": not partial, "quote_total": (doc or {}).get("summary", {}).get("grand_total")}

    def reprice(self, plan_id: str, today: date | None = None) -> ReplanOutcome:
        """Price the plan again with the quotes the user has confirmed since it was made, as one new version. A confirmed booking keeps its vendor and takes the price from the quote."""
        return self.replan(plan_id, Disruption(kind=DisruptionKind.QUOTE_UPDATE), today)

    def _read_attachment(self, vendor_id: str, name: str, data: bytes) -> str | None:
        try:
            out = self.read_quote_document(vendor_id, name, data)
        except ValueError:  # includes DocumentError: not a readable quote file
            return None
        return out["document"] if out["lines"] else None

    def confirm_quote_line(self, vendor_id: str, document: str, line: str, valid_days: int, plan_id: str | None = None):
        """The user's tap on a line read from a quote file ("1".."n", or "grand_total"). The amount is the one this app extracted and kept, never one the caller sends, and
        a line that does not say what it covers cannot be confirmed. It becomes a quote observation the user vouches for, valid for the days they choose. Given the plan it is
        confirmed for, it is recorded for that plan's event date and prices that event; without one the date is unknown and the quote is history, never a price (a quote prices only the event it was given for)."""
        if not (isinstance(valid_days, int) and not isinstance(valid_days, bool) and 1 <= valid_days <= 90):
            raise ValueError("say how many days (1-90) this price should be treated as valid")
        if not re.fullmatch(r"[0-9a-f]{10}", document or "") or not re.fullmatch(r"grand_total|[1-9][0-9]{0,2}", line or ""):
            raise ValueError("no such quote line")
        field = f"quote.{document}.{'grand_total' if line == 'grand_total' else 'l' + line}"
        rec = next((r for r in reversed(self.evidence.records(vendor_id, field)) if r.source == f"doc:{document}" and r.state is FactState.EXTRACTED), None)
        if rec is None or not isinstance(rec.value, dict):
            raise ValueError("no such quote line")
        amount = rec.value.get("total_inr")
        if not isinstance(amount, int) or amount <= 0:
            raise ValueError("the document does not say what this price covers, so it cannot be confirmed as a price")
        category = next((c for c in VendorCategory if self.repo.get(c, vendor_id)), None) or getattr(self.externals.get(vendor_id), "category", None)
        if category is None:
            raise ValueError("unknown vendor")
        now = self.outreach_store.now()
        for_date = self._scope(plan_id).request.event_date if plan_id else None
        obs = self.observe_quote(
            vendor_id, category, source_type=ObservationSource.VENDOR_QUOTE, entered_by="user_confirmed", observed_at=now, expires_at=(now + timedelta(days=valid_days)).date(),
            confidence=0.7, evidence_ref=f"doc:{document} {line}", amount_inr=amount, basis="total", quoted_for_date=for_date,
            note=f"{str(rec.value.get('label', ''))[:80]}: from a quote file; valid {valid_days} days, your choice at confirmation"[:200])
        self.evidence.record(vendor_id, field, rec.value, FactState.USER_CONFIRMED, "user_input")
        return obs

    def purge_logs(self, max_age_days: int | None = None) -> list[str]:
        """Delete run logs older than the retention period (default 90 days, ``EVENTMATE_LOG_RETENTION_DAYS``) and kept voice notes older than theirs. Returns what was removed."""
        return purge_old_logs(self.logs.decisions.log_dir, max_age_days) + [f"voice note {n}" for n in self.voice_notes.purge_expired()]

    def ledger_add(self, plan_id: str, kind: str, category: VendorCategory, amount: int, note: str = "") -> LedgerEntry:
        """Record money the user reports (quote, payment, refund) against the latest plan. Nothing is paid by to;doh-the event planner."""
        return self.ledger.add(self.store.latest(plan_id), kind, category, amount, note)

    def ledger_entries(self, plan_id: str):
        self.store.latest(plan_id)  # unknown / hostile ids fail here
        return self.ledger.entries(plan_id)

    def ledger_summary(self, plan_id: str) -> LedgerSummary:
        return self.ledger.summarize(self.store.latest(plan_id))

    def observe_quote(self, vendor_id: str, category: VendorCategory, **fields):
        """Record a vendor quote you were given. Later plans and validation price from it."""
        return self.observations.add_quote(vendor_id, category, **fields)

    def earlier_quotes(self, city: str, today: date, guests: int | None = None, event_date: date | None = None) -> list[dict]:
        """The last quote each discovered business in a city gave, for showing while a fresh one is awaited: what it said, when, how many earlier ones there are, and why it may no longer hold.
        Reads only. An earlier quote is shown, never priced from."""
        out = []
        for biz in self.externals.all():
            if biz.city.casefold() != city.casefold():
                continue
            quotes = self.observations.quotes(biz.vendor_id)
            if not quotes:
                continue
            last = quotes[-1]
            out.append({
                "vendor_id": biz.vendor_id, "external_id": biz.external_id, "name": biz.name, "category": biz.category.value, "amount_inr": last.amount_inr, "basis": last.basis,
                "guest_count": last.guest_count, "quoted_for_date": last.quoted_for_date.isoformat() if last.quoted_for_date else None, "observed_at": last.observed_at.isoformat(),
                "earlier": len(quotes) - 1, "old": old_reasons(last, today, guests, event_date),
            })
        return out

    def vendor_scores(self, category: VendorCategory, today: date | None = None):
        """Transparent score of every vendor with a current quote in a category. Reads only; changes no price."""
        return score_category(self.observations, category, today or date.today())

    def quote_status(self, plan_id: str, today: date | None = None) -> list[SelectionQuoteStatus]:
        """Quote report, staleness and confirmation source for each selection of the latest plan."""
        today = today or date.today()
        self._clock(today)
        plan = self.store.latest(plan_id)
        rows = []
        for s in plan.selections:
            rec = self.repo.get(s.category, s.vendor_id)
            current = self.repo.price(rec, plan.request.guest_count, plan.request.event_date) if rec else s.cost
            confirmed = s.booking_status == BookingStatus.CONFIRMED
            by = None
            if confirmed:
                by = "vendor" if self.observations.vendor_confirmed(s.vendor_id) else "you"
            rows.append(SelectionQuoteStatus(
                category=s.category, vendor_id=s.vendor_id, vendor_name=s.name, booking_status=s.booking_status,
                plan_cost=s.cost, current_price=current, price_stale=current != s.cost, confirmed_by=by,
                report=self.observations.report(s.vendor_id, today),
            ))
        return rows

    def discover_venues(self, city: str, kind: str = "venue", limit: int = 20):
        """Places mapped on OpenStreetMap: candidates to ask about, never vendor-database facts."""
        return self.discovery.venues(city, kind, limit)

    def weather_forecast(self, city: str, day: date):
        """Forecast from OpenWeather. Past its horizon the answer says so; nothing is estimated."""
        return self.weather.forecast(city, day)

    def weather_advice(self, plan_id: str):
        """Forecast for the event day plus code-built advice for the chosen venue's setting. Changes nothing."""
        plan = self.store.latest(plan_id)
        venue = plan.selection_for(VendorCategory.VENUE)
        rec = self.repo.get(VendorCategory.VENUE, venue.vendor_id) if venue else None
        setting = getattr(rec, "setting", None)
        forecast = self.weather.forecast(plan.request.city, plan.request.event_date)
        return WeatherAdvice(plan_id=plan_id, venue_setting=setting, items=tuple(advise(forecast, setting)), forecast=forecast)

    def plan_weather(self, plan_id: str):
        plan = self.store.latest(plan_id)
        return self.weather.forecast(plan.request.city, plan.request.event_date)

    def save_lead(self, plan_id: str, **fields):
        """Bookmark an advertised price against a plan. It is not a quote and changes no price."""
        self.store.latest(plan_id)  # unknown or hostile ids fail here
        return self.lead_store.add(plan_id, **fields)

    def leads(self, plan_id: str):
        self.store.latest(plan_id)
        return self.lead_store.list(plan_id)

    @property
    def jobs(self) -> JobWorker:
        """The follow-through worker. The job database is created on first use."""
        if self._jobs is None:
            self._jobs = JobWorker(self, JobStore(self.store.root), self.jobs_clock) if self.jobs_clock else JobWorker(self, JobStore(self.store.root))
        return self._jobs

    def _job_guard(self, draft) -> str | None:
        """Dispatch-time veto, independent of the standing permission: an automatic follow-up that belongs to a finished or cancelled job never goes out."""
        if draft.kind != "followup" or not (self.store.root / "jobs.db").exists():
            return None
        if self._job_store is None:
            self._job_store = JobStore(self.store.root)
        for job in self._job_store.list(draft.plan_id):
            if any(p.draft_id == draft.parent_id for p in job.vendors.values()):
                return "the job this belongs to has ended" if job.status in ("done", "expired", "cancelled") else None
        return None

    def _jobs_of(self, plan_id: str):
        return self.jobs.store.list(plan_id) if (self.store.root / "jobs.db").exists() else []

    def _own_addresses(self) -> set:
        """The mailboxes to;doh-the event planner itself sends from."""
        return {a for a in (getattr(getattr(ch, "config", None), "from_addr", "") for ch in self.conversations.channels.values()) if a}

    def _vendor_names(self):
        names = {p.id: p.name for c in VendorCategory for p in self.repo.providers(c)}
        for b in self.externals.all():
            names[b.vendor_id] = f"{b.name} (TEST VENDOR, address owned by {b.owner_confirmed_by})" if b.is_test else b.name  # a test vendor is labelled wherever it is named
        return names

    def _vendor_addresses(self) -> set:
        """Every email address to;doh-the event planner has written to or saved for a vendor: none of these may ever be the owner's notification address."""
        out = {c.email for c in self.outreach_store.contacts().values() if c.email}
        for pid in self.outreach_store.plan_ids():
            out |= {d.recipient for d in self.outreach_store.list(pid) if d.channel == "email"}
        return out

    def background_round(self):
        """One round of the agent's background work: the job worker if any job exists (it polls conversations itself), otherwise just the conversations,
        then any page watches that are due (they only read public pages; the owner's kill switch stops them too)."""
        report = self.jobs.tick() if (self.store.root / "jobs.db").exists() else self.poll_conversations()
        if self.watches.exists():
            self.last_watch_report = self.watch_runner().tick()  # kept here as well: the round's own result may not be a dict, and a watch's errors must not vanish
            if isinstance(report, dict):
                report["watches"] = self.last_watch_report
        return report

    # ------------------------------------------------------------ page watches

    def watch_runner(self) -> WatchRunner:
        return WatchRunner(self.watches, self.contact_fetcher, self.evidence, killed=lambda: self.outreach_store.killed, now=lambda: self.outreach_store.now())  # one clock for adding and checking

    def add_watch(self, subject: str, url: str, condition: str, text: str | None = None, amount_inr: int | None = None, interval_minutes: int = 1440):
        """Watch one public page for the owner. The subject must be something this workspace knows (a plan, a discovered business or a vendor)."""
        known = (
            subject in self.store.list_plans()
            or self.externals.get(subject) is not None
            or any(self.repo.get(c, subject) for c in VendorCategory)
        )
        if not known:
            raise ValueError("unknown subject: use a plan id or a vendor id from this workspace")
        return self.watches.add(subject, url, condition, self.outreach_store.now(), text, amount_inr, interval_minutes)

    def resume_watch(self, watch_id: str):
        w = self.watches.get(watch_id)
        return self.watches.save(w.model_copy(update={"status": "active", "failures": 0, "last_error": "", "next_check_at": self.outreach_store.now()}))

    def _scope(self, plan_id: str):
        """What a sourcing step reads its facts from: a stored plan, or, for ``brief_<id>``, the staged brief that has no plan yet. Unknown, hostile or timed-out ids fail here."""
        if brief_scope.is_brief(plan_id):
            built = self.brief_links.plan_of(plan_id)
            if built is not None:  # the brief became a plan: the plan is the authority on the event from then on, and outlives the brief's row
                return self.store.latest(built)
            row = self.staging.require(brief_scope.staged_id_of(plan_id))
            if row.state == "expired":
                raise StageGone("that brief timed out; start it again")
            return brief_scope.scope_of(row)
        return self.store.latest(plan_id)

    def _reply_landed(self, plan_id: str) -> None:
        """A reply came in under this brief: keep the brief a while longer, so what the user does with it has a brief to act on."""
        if brief_scope.is_brief(plan_id) and self.brief_links.plan_of(plan_id) is None:
            try:
                self.staging.keep(brief_scope.staged_id_of(plan_id))
            except StageGone:
                pass

    def reply_reviews_open(self) -> list:
        return self.reply_reviews.open()

    def assign_reply(self, review_id: str, plan_id: str):
        """The user's choice of event for a held reply."""
        return self.conversations.assign_review(review_id, plan_id)

    def dismiss_reply_review(self, review_id: str):
        return self.conversations.dismiss_review(review_id)

    def _brief_awaits_replies(self, staged_id: str) -> bool:
        """A request was sent under this brief and nothing has come back, within the time replies are still looked for: the brief is kept, so a late reply has somewhere to land."""
        pid = brief_scope.BRIEF_PREFIX + staged_id
        since = self.outreach_store.now() - LOOKBACK
        sent = [d for d in self.outreach_store.list(pid) if d.kind == "request" and d.status == "sent" and d.sent_at is not None and d.sent_at >= since]
        return bool(sent) and not self.outreach_store.inbound(pid)

    def draft_outreach(self, plan_id: str, category: VendorCategory, channel: str, recipient: str, sender_name: str, vendor_id: str | None = None):
        """Write a quote request for a vendor in the plan, or for another vendor of the category when ``vendor_id`` is given (to compare quotes).
        It is a proposal: nothing leaves until it is approved."""
        vendor = None
        if vendor_id is not None:
            rv = self.resolve_vendor(vendor_id, category)
            if rv.ref.kind == "external":
                self._require_confirmed_address(rv, channel, recipient)
            vendor = (rv.ref.id, rv.name, rv.origin)
        hit = self.suppressions.active_for(channel, recipient) if channel in ("email", "whatsapp") else None
        if hit is not None:
            raise ValueError("Blocked: do not contact. That address is on the do-not-contact list (" + hit.reason.replace("_", " ") + "); only you can lift it.")
        scope = self._scope(plan_id)
        draft = self.outreach_store.draft(scope, category, channel, recipient, sender_name, vendor)
        if getattr(scope, "is_brief", False):
            self.staging.keep(brief_scope.staged_id_of(plan_id))  # the replies will be filed under this brief
        return draft

    def outreach(self, plan_id: str):
        self._scope(plan_id)
        return self.outreach_store.list(plan_id)

    def delivery_of(self, d, replied: set[str], bounced: set[str], today: date | None = None) -> dict:
        """Where a request stands in words that do not say more than is known: written, queued, accepted by the mail server (not proof of delivery), bounced, replied (A5)."""
        key = d.parent_id or d.draft_id
        table = {
            "prepared": ("draft", "Written, waiting for your approval"), "approved": ("queued", "Approved and queued; not handed to the mail server yet"), "sending": ("queued", "Being handed to the mail server now"),
            "failed": ("failed", "The mail server refused it; nothing was delivered"), "outcome_unknown": ("unknown", "Handed to the mail server, but the connection failed: it may or may not have gone. Check your Sent folder before trying again"),
            "denied": ("denied", "You chose not to send it"), "expired": ("expired", "Expired before it was approved"), "blocked": ("blocked", "Not sent: the address is on the do-not-contact list"),
            "stale": ("stale", "Not sent: the brief changed after it was written"), "marked_sent": ("self", "You sent it yourself; the app cannot tell whether it arrived"),
        }
        if d.status == "sent":
            if d.external_id and d.external_id in bounced:
                return {"state": "bounced", "label": "Bounced: the mail server reported it could not be delivered"}
            if key in replied:
                return {"state": "replied", "label": "Replied"}
            word = "WhatsApp accepted it" if d.channel == "whatsapp" else "Accepted by the mail server"
            return {"state": "accepted", "label": f"{word}. That is not proof it was delivered or read", **self._silence(d.sent_at, key in replied, today)}
        state, label = table.get(d.status, (d.status, d.status))
        if d.status == "marked_sent":
            return {"state": state, "label": label, **self._silence(d.marked_at, key in replied, today)}
        return {"state": state, "label": label}

    SILENT_AFTER_DAYS = 3  # a request with no reply after this many days is shown as quiet, so the user can follow up themselves (never by the app on its own)

    def _silence(self, since, replied: bool, today: date | None) -> dict:
        """How long a request has been waiting, once it has waited long enough to mention. Words only: the app chases nobody by itself."""
        if replied or since is None:
            return {}
        days = ((today or self.outreach_store.now().date()) - since.date()).days
        return {"waiting_days": days, "silent": True} if days >= self.SILENT_AFTER_DAYS else {}

    def outreach_with_delivery(self, plan_id: str) -> list[dict]:
        drafts = self.outreach(plan_id)
        replied = {m.draft_id for m in self.outreach_store.inbound(plan_id)}
        bounced = {s.source_ref for s in self.suppressions.all() if s.reason == "bad_address"}
        return [{**json.loads(d.model_dump_json()), "delivery": self.delivery_of(d, replied, bounced)} for d in drafts]

    def mark_outreach_sent(self, plan_id: str, draft_id: str):
        """The manual path: the user sent it themselves from the link."""
        self._scope(plan_id)
        return self.outreach_store.mark_sent(plan_id, draft_id)

    def approve_outreach(self, plan_id: str, draft_id: str, content_hash: str):
        """Approve exactly this text; the agent sends it through the configured channel."""
        self._scope(plan_id)
        return self.conversations.approve(plan_id, draft_id, content_hash)

    def deny_outreach(self, plan_id: str, draft_id: str):
        self._scope(plan_id)
        return self.conversations.deny(plan_id, draft_id)

    def followup_outreach(self, plan_id: str, request_id: str, text: str, sender_name: str):
        self._scope(plan_id)
        return self.outreach_store.followup(plan_id, request_id, text, sender_name)

    def request_all_quotes(self, plan_id: str, sender_name: str):
        scope = self._scope(plan_id)
        if getattr(scope, "is_brief", False):
            return self._request_all_for_brief(scope, sender_name)
        return self.conversations.request_all(plan_id, sender_name)

    def _request_all_for_brief(self, scope, sender_name: str):
        """One proposal per shortlisted business that has a contact the user confirmed and no live request. The user still approves each one; nothing is sent here."""
        existing = self.outreach_store.list(scope.plan_id)
        made, skipped = [], []
        for item in self.shortlist_view(scope.plan_id):
            biz, c = item.business, item.contact
            live = any(d.vendor_id == biz.vendor_id and d.kind == "request" and d.status in ("prepared", "approved", "sending", "sent") for d in existing)
            if live:
                skipped.append(f"{biz.name}: already asked")
            elif c is None:
                skipped.append(f"{biz.name}: no confirmed contact")
            elif not (address := c.email if c.preferred == "email" else c.whatsapp):
                skipped.append(f"{biz.name}: no {c.preferred} address saved")
            elif item.blocked:
                skipped.append(f"{biz.name}: do not contact")
            else:
                to = address if c.preferred == "email" else "+" + address
                made.append(self.outreach_store.draft(scope, biz.category, c.preferred, to, sender_name, (biz.vendor_id, biz.name, biz.origin)))
        if made:
            self.staging.keep(brief_scope.staged_id_of(scope.plan_id))
        return made, skipped

    def inbox(self, plan_id: str):
        return self.conversations.inbox(plan_id)

    def poll_conversations(self):
        """Send what is approved and read replies. Called by the background loop and by the CLI."""
        return self.conversations.poll()

    def grant_autoreply(self, plan_id: str, sender_name: str, hours: int = 72, max_per_thread: int = 3, max_total: int = 10):
        """Standing permission for fixed follow-up questions, bounded and revocable."""
        return self.conversations.grant_autoreply(plan_id, sender_name, hours, max_per_thread, max_total)

    def revoke_autoreply(self, plan_id: str):
        return self.conversations.revoke_autoreply(plan_id)

    def autoreply_status(self, plan_id: str):
        self.store.latest(plan_id)
        return self.conversations.autoreply_status(plan_id)

    def pause_sending(self):
        """Hold everything that would leave (messages a person approved, a standing permission's, the owner's emails). Does not touch the kill switch."""
        self.conversations.pause()

    def held_messages(self) -> list[dict]:
        """Approved messages waiting for a resume: who, how, the subject and how old the approval is. Never the body."""
        now = self.conversations.now()
        return [{"plan_id": d.plan_id, "draft_id": d.draft_id, "vendor": d.vendor_name, "channel": d.channel, "subject": d.subject, "approved_by": d.approved_by,
                 "approved_hours_ago": round((now - d.decided_at).total_seconds() / 3600, 1) if d.decided_at else None} for d in self.conversations.held()]

    def held_snapshot(self) -> str:
        return self.conversations.snapshot_of(self.conversations.held())

    def lapsed_messages(self) -> list[dict]:
        """Approvals that ran out before their message went. They will not be sent: they need a fresh draft."""
        return [{"plan_id": d.plan_id, "draft_id": d.draft_id, "vendor": d.vendor_name, "channel": d.channel, "subject": d.subject} for d in self.conversations.lapsed()]

    def resume_sending(self, snapshot: str | None = None) -> dict:
        """Lift the pause for exactly the queue the caller reviewed (``held_snapshot``): a different queue, even one of the same size, releases nothing (``HeldMessages``). The check and the lift are
        atomic against every approval and every sender (``ConversationService.release``). The reviewed messages then go through the same limits and checks as any message: the daily and per-vendor
        caps, the plan still matching the text, the do-not-contact list and the 48-hour approval, so some may wait or be stopped. Returns how many were queued and what happened to each; it does not touch the kill switch."""
        reviewed = self.conversations.release(snapshot)
        sent, waiting, stopped = 0, 0, []
        for d in reviewed:
            now = self.outreach_store.get(d.plan_id, d.draft_id)
            if now is not None and now.status in ("sent", "marked_sent"):
                sent += 1
            elif now is None or now.status in ("approved", "sending"):
                waiting += 1
            else:
                stopped.append({"vendor": d.vendor_name, "channel": d.channel, "subject": d.subject, "status": now.status, "why": (now.error or "")[:200]})
        return {"queued": len(reviewed), "sent": sent, "waiting": waiting, "stopped": stopped}

    def kill_all_autoreplies(self):
        self.conversations.kill_all()

    def resume_autoreplies(self):
        self.conversations.resume_all()

    def reply_suggestions(self, plan_id: str, request_id: str):
        self.store.latest(plan_id)
        return self.conversations.suggestions(plan_id, request_id)

    def mark_reply_read(self, plan_id: str, message_id: str):
        self.store.latest(plan_id)
        return self.outreach_store.mark_read(plan_id, message_id)

    def resolve_vendor(self, vendor_id: str, category: VendorCategory | None = None) -> ResolvedVendor:
        """The one place a vendor id becomes a vendor. A seed id is looked up in the vendor database, an ``ext_`` id only in the external store; nothing crosses over."""
        ref = VendorRef.of(vendor_id)  # a malformed id fails here
        if ref.kind == "external":
            biz = self.externals.get(vendor_id)
            if biz is None:
                raise ValueError(f"unknown {category.value if category else 'vendor'} {vendor_id!r}")
            if category is not None and biz.category != category:
                raise ValueError(f"{biz.name} is a {biz.category.value}, not a {category.value}")
            return ResolvedVendor(ref, biz.name, biz.category, biz.origin, biz.source_url or biz.website)
        for c in ([category] if category else list(VendorCategory)):
            rec = self.repo.get(c, vendor_id)
            if rec is not None:
                return ResolvedVendor(ref, rec.name, c, "seed", None)
        if category is not None and any(self.repo.get(c, vendor_id) for c in VendorCategory):
            raise ValueError(f"{vendor_id} is not a {category.value}")
        raise ValueError(f"unknown {category.value if category else 'vendor'} {vendor_id!r}")

    def _require_confirmed_address(self, rv: ResolvedVendor, channel: str, recipient: str) -> None:
        """A discovered business is written to only at an address the user confirmed for it. A typed or extracted address is not enough."""
        c = self.outreach_store.contacts().get(rv.ref.id)
        if c is None or not c.confirmed_by:
            raise ValueError(f"{rv.name} has no confirmed contact address yet: confirm one that was found on its own page or listing")
        have = c.email if channel == "email" else c.whatsapp
        want = recipient.strip().casefold() if channel == "email" else "".join(ch for ch in recipient if ch.isdigit())
        if have is None or have.casefold() != want:
            raise ValueError(f"that recipient is not the confirmed address for {rv.name}")

    def set_vendor_contact(self, vendor_id: str, email: str | None, whatsapp: str | None, preferred: str):
        """The user types a contact for a seed vendor. A discovered business gets its contact only by confirming a found address."""
        if VendorRef.of(vendor_id).kind == "external":
            raise ValueError("a discovered business's contact is saved only by confirming an address found on its own page or listing, or as a test vendor")
        if not any(self.repo.get(c, vendor_id) for c in VendorCategory):
            raise ValueError(f"unknown vendor {vendor_id!r}")
        return self.outreach_store.set_contact(vendor_id, email, whatsapp, preferred)

    def add_discovered(self, external_id: str, name: str, city: str, category: VendorCategory, source: str, source_url: str | None = None,
                       website: str | None = None, rating: float | None = None) -> ExternalBusiness:
        """Record a business that discovery found. It is not a vendor record and is never promoted to one."""
        if source not in ("osm", "gplaces"):
            raise ValueError("a discovered business comes from OpenStreetMap or Google Places")
        return self.externals.add(external_id, name, city, category, source, source_url, website, rating)

    def add_test_vendor(self, name: str, city: str, category: VendorCategory, email: str, owns_address: bool, confirmed_by: str, plan_id: str | None = None):
        """A vendor for testing whose address is the user's own. Needs the user's explicit statement that they own it, and is labelled TEST VENDOR everywhere."""
        if owns_address is not True or not (confirmed_by or "").strip():
            raise ValueError("a test vendor needs your confirmation that you own the address")
        email = clean_email(email)
        name = clean_name(name)
        if not name:
            raise ValueError("give the test vendor a name")
        biz = self.externals.add(f"test:{slug(name)}-{hashlib.sha256(email.casefold().encode()).hexdigest()[:8]}",
                                 name, city, category, "test", owner_confirmed_by=confirmed_by.strip())
        now = self.outreach_store.now()
        contact = self.outreach_store.set_contact_record(VendorContact(
            vendor_id=biz.vendor_id, email=email, preferred="email", updated_at=now, origin="test", confirmed_by=confirmed_by.strip(), confirmed_at=now))
        if plan_id is not None:
            self._scope(plan_id)
            self.shortlist.add_entry(plan_id, category, biz.vendor_id)
        return biz, contact

    def add_user_vendor(self, name: str, city: str, category: VendorCategory, email: str | None, whatsapp: str | None, by: str, plan_id: str | None = None):
        """A business the user knows and adds themselves, with the contact they typed. Nothing is sent to it until they approve each message, and the do-not-contact list applies as it does to every business."""
        by = (by or "").strip()
        if not 1 <= len(by) <= 60:
            raise ValueError("say who is adding this vendor (a name)")
        name = clean_name(name)
        if not name:
            raise ValueError("give the vendor a name")
        if not (email or "").strip() and not (whatsapp or "").strip():
            raise ValueError("give an email address or a WhatsApp number to reach them on")
        email = clean_email(email) if (email or "").strip() else None
        phone = normalise_phone(whatsapp) if (whatsapp or "").strip() else None
        for channel, address in (("email", email), ("whatsapp", phone)):
            hit = self.suppressions.active_for(channel, address) if address else None
            if hit is not None:
                raise ValueError("Blocked: do not contact. That address is on the do-not-contact list (" + hit.reason.replace("_", " ") + "); only you can lift it.")
        scope = self._scope(plan_id) if plan_id is not None else None
        biz = self.externals.add(f"user:{slug(name)}-{hashlib.sha256((email or phone).casefold().encode()).hexdigest()[:8]}", name, city or (scope.request.city if scope else ""), category, "user", owner_confirmed_by=by)
        now = self.outreach_store.now()
        contact = self.outreach_store.set_contact_record(VendorContact(
            vendor_id=biz.vendor_id, email=email, whatsapp=phone, preferred="email" if email else "whatsapp", updated_at=now, origin="user", confirmed_by=by, confirmed_at=now))
        if plan_id is not None:
            self.shortlist.add_entry(plan_id, category, biz.vendor_id)
        return biz, contact

    def vendor_directory(self) -> dict:
        """Every vendor id the app can show, with its plain name and where it came from (seed, discovered, test). The UI never shows a raw id."""
        d = {p.id: {"name": p.name, "origin": "seed", "label": None} for c in VendorCategory for p in self.repo.providers(c)}
        for b in self.externals.all():
            d[b.vendor_id] = {"name": b.name, "origin": b.origin, "label": f"TEST VENDOR (address owned by {b.owner_confirmed_by})" if b.is_test else (f"Added by {b.owner_confirmed_by}" if b.source == "user" else None)}
        return d

    def vendor_labels(self) -> dict:
        """Labels the UI must show next to a business: a test vendor says so and whose address it is. Seed vendors and discovered businesses have none."""
        return {b.vendor_id: f"TEST VENDOR (address owned by {b.owner_confirmed_by})" for b in self.externals.all() if b.is_test}

    def search_vendors(self, plan_id: str, category: VendorCategory, source: str | None = None, limit: int = 10) -> SearchOutcome:
        """Find businesses for a plan's city and category and shortlist them. Not vendor records, and nothing is contacted."""
        return self.vendor_search.search(self._scope(plan_id), category, source, limit)

    def shortlist_view(self, plan_id: str, category: VendorCategory | None = None) -> list[ShortlistItem]:
        self._scope(plan_id)
        contacts = self.outreach_store.contacts()
        out = []
        for e in self.shortlist.entries(plan_id, category):
            biz = self.externals.get(e.vendor_id)
            if biz is None:
                continue
            c = contacts.get(e.vendor_id)
            confirmed = c if c is not None and c.confirmed_by else None
            hit = self.suppressions.active_for("email", confirmed.email) if confirmed and confirmed.email else None
            out.append(ShortlistItem(
                category=e.category, business=biz, candidates=tuple(self.shortlist.candidates(e.vendor_id)), contact=confirmed,
                blocked=None if hit is None else ("do not contact: " + hit.reason.replace("_", " ")),
            ))
        return out

    def _reply(self, plan_id: str, message_id: str) -> InboundMessage:
        msg = next((m for m in self.outreach_store.inbound(plan_id) if m.message_id == message_id), None)
        if msg is None or msg.category not in quote_template.ASKED:
            raise ValueError("no such reply for this brief or plan")
        return msg

    def quote_assessments(self, plan_id: str) -> list[QuoteAssessment]:
        """Each vendor reply read as a quote: field by field with the vendor's own line, what the user has confirmed, how complete it is, and what does not fit the event. Newest first."""
        req = self._scope(plan_id).request
        diets = tuple(d.value for d in req.dietary)
        found = [self._with_attachments(assess(m, m.category, self.quote_confirmations.latest(plan_id, m.message_id), req.guest_count, diets), m)
                 for m in self.outreach_store.inbound(plan_id) if m.category in quote_template.ASKED]
        return sorted(found, key=lambda a: a.received_at, reverse=True)

    def _with_attachments(self, a: QuoteAssessment, msg: InboundMessage) -> QuoteAssessment:
        """Put the lines of the quote files attached to this reply beside it (``quotes-attachments``). Only files the reader already read from this very reply: the lines are evidence for the user to pick an amount from."""
        if not msg.quote_documents:
            return a
        docs = {d["document"]: d for d in self.quote_documents(msg.vendor_id)}
        attached = tuple(
            AttachedQuote(document=i, grand_total_inr=docs[i]["summary"].get("grand_total"),
                          lines=tuple(DocLine(label=str(ln.get("label", ""))[:120] or "line", quantity=ln.get("quantity"), unit_inr=ln.get("unit_inr"), total_inr=ln.get("total_inr")) for ln in docs[i]["lines"][:40]))
            for i in msg.quote_documents if i in docs)
        return a.model_copy(update={"attached": attached}) if attached else a

    def promote_quote(self, plan_id: str, message_id: str, by: str, setting: str | None = None) -> QuoteAssessment:
        """The user asks for a fully confirmed quote to be a vendor the planner may choose, for this city and date only (the exception to ADR-028 and ADR-049). Refused unless every required field is
        confirmed, the vendor said it is available, its price is a recorded quote that has not run out, and the business is a discovered one (a seed vendor is already plannable)."""
        scope = self._scope(plan_id)
        by = (by or "").strip()
        if not 1 <= len(by) <= 60:
            raise ValueError("say who is asking for this (a name)")
        msg = self._reply(plan_id, message_id)
        if VendorRef.of(msg.vendor_id).kind != "external":
            raise ValueError("this vendor is already in the database: confirm the quote and the planner uses it")
        a = next(x for x in self.quote_assessments(plan_id) if x.message_id == message_id)
        q = self.observations.quote_for_reply(msg.vendor_id, message_id)  # this reply's own quote, not whatever the vendor quoted last
        if q is None:
            raise ValueError("confirm this reply's price and how long it holds first: that records the quote the plan will use")
        if self.repo.clock() > q.expires_at:
            raise ValueError("this quote has run out: ask the vendor for a new one")
        req = scope.request
        if q.quoted_for_date is not None and q.quoted_for_date != req.event_date:
            raise ValueError(f"this quote was given for {q.quoted_for_date.isoformat()}, not {req.event_date.isoformat()}: ask the vendor for a quote for this date")
        provider = to_provider(a, msg.vendor_id, self.externals.get(msg.vendor_id).name, req.city, req.event_date, setting)
        self.quoted.add(message_id, provider, by)
        return a

    def log_contact(self, plan_id: str, vendor_id: str, channel: str, by: str, note: str = ""):
        """The user says they contacted this business themselves (their own mail, WhatsApp, a call, a visit). Sends nothing; the card shows it and the reply can be pasted in."""
        self._scope(plan_id)
        by = (by or "").strip()
        if not 1 <= len(by) <= 60:
            raise ValueError("say who made the contact (a name)")
        if channel not in ("email", "whatsapp", "phone", "in_person"):
            raise ValueError("say how: email, whatsapp, phone or in person")
        if len(note or "") > 200:
            raise ValueError("keep the note to 200 characters")
        if not any(i.business.vendor_id == vendor_id for i in self.shortlist_view(plan_id)):
            raise ValueError("that business is not on this list")
        return self.contact_log.add(plan_id, vendor_id, channel, by, note or "")

    def service_status(self) -> list[dict]:
        """How each outside service is doing, from what the app has seen of it: ok, quota used, degraded, not configured, or not used yet."""
        cfg = {"places": getattr(self.places, "configured", None), "weather": getattr(self.weather, "configured", None)}
        return HEALTH.snapshot({k: (v() if callable(v) else v) for k, v in cfg.items() if v is not None})

    def propose_payment_terms(self, plan_id: str, message_id: str, by: str):
        """Money terms for a vendor's quote the user has confirmed in full (price and deposit): the deposit, the balance, and the vendor's own sentence about the deposit. They are only a proposal until
        the user says who is paid and how (``confirm_payment_terms``). The plan must exist: nothing is paid for a brief."""
        plan = self.store.latest(plan_id)
        by = (by or "").strip()
        if not 1 <= len(by) <= 60:
            raise ValueError("say who is asking for this (a name)")
        msg = self._reply(plan_id, message_id)
        a = next(x for x in self.quote_assessments(plan_id) if x.message_id == message_id)
        if not a.comparable:
            raise ValueError("confirm every line of this quote first, the price and the deposit included: payment terms are made only from a quote you confirmed in full")
        fields = {f.key: f for f in a.fields}
        price = int(fields[quote_template.PRICE_KEY[msg.category]].value)
        total = price * plan.request.guest_count if msg.category == VendorCategory.CATERER else price
        deposit = int(fields["deposit"].value)
        return self.payments.propose_terms(plan_id, msg.category, msg.vendor_id, msg.vendor_name, message_id, total, deposit, fields["deposit"].evidence or "", by)

    def payment_view(self, plan_id: str) -> dict:
        """Everything about money for a plan: the terms, the payments and what was done to them, the ledger beside them, and the plain statement that the app moves nothing by itself."""
        self.store.latest(plan_id)
        self.payments.settle_stuck(plan_id)  # a payment a dead process was carrying out is shown as unknown, not as still going
        return {"terms": [json.loads(t.model_dump_json()) for t in self.payments.terms.all(plan_id)], "payments": [json.loads(p.model_dump_json()) for p in self.payments.proposals.all(plan_id)],
                "events": [json.loads(e.model_dump_json()) for e in self.payments.proposals.events(plan_id)], "external": sorted(k for k in self.payments.providers() if k != "manual") if self.payments.external_allowed else [],
                "notice": "to;doh-the event planner records and instructs. It does not hold or move your money by itself, a quote is not a booking, and it does not guarantee that a vendor delivers."}

    def unpromote_quote(self, plan_id: str, message_id: str, by: str) -> QuoteAssessment:
        """The user takes back "plan with this vendor". The planner no longer sees the vendor from this reply; the quote stays recorded and confirmed, and the choice can be made again. A plan already
        built keeps what it was built with, but planning again will not include this vendor until it is chosen again."""
        self._scope(plan_id)
        by = (by or "").strip()
        if not 1 <= len(by) <= 60:
            raise ValueError("say who is taking this back (a name)")
        msg = self._reply(plan_id, message_id)
        if self.quoted.withdraw(message_id, by) is None:
            raise ValueError("this vendor is not chosen from this reply")
        self.repo.quoted = self.quoted.providers  # the planner reads the store afresh
        return next(x for x in self.quote_assessments(plan_id) if x.message_id == msg.message_id)

    def paste_reply(self, plan_id: str, vendor_id: str, text: str, by: str, channel: str = "email") -> InboundMessage:
        """A reply the user got outside the app (their own mail, a call, WhatsApp) and typed or pasted in. It is read and confirmed field by field exactly like one that arrived by mail, but it is
        marked as the user's own words: nothing in it is ever shared with another person, because anyone could write anything here."""
        from eventmate.messaging.conversations import analyze_reply
        self._scope(plan_id)
        by = (by or "").strip()
        if not 1 <= len(by) <= 60:
            raise ValueError("say who is entering this (a name)")
        body = "\n".join(line.rstrip() for line in (text or "").replace("\r\n", "\n").split("\n")).strip()
        if len(body) < 3:
            raise ValueError("paste what the vendor wrote")
        if len(body) > 4000:
            raise ValueError("that is longer than 4,000 characters: paste just the part with the answers")
        if channel not in ("email", "whatsapp"):
            raise ValueError("channel must be email or whatsapp")
        item = next((i for i in self.shortlist_view(plan_id) if i.business.vendor_id == vendor_id), None)
        if item is None:
            raise ValueError("that business is not on this list")
        biz = item.business
        asked = [d for d in self.outreach_store.list(plan_id) if d.vendor_id == vendor_id and d.kind == "request"]
        msg = InboundMessage(
            message_id=uuid.uuid4().hex, plan_id=plan_id, draft_id=asked[-1].draft_id if asked else "pasted", vendor_id=vendor_id, vendor_name=biz.name, vendor_origin=biz.origin, category=biz.category,
            channel=channel, sender=f"entered by {by}"[:254], received_at=self.outreach_store.now(), external_id=f"pasted:{uuid.uuid4().hex}", body=body,
            analysis=analyze_reply(body, self.outreach_store.checklists.get(biz.category.value, ())), pasted_by=by, match_method="pasted", auto_handled=True,
        )
        stored = self.outreach_store.add_inbound(msg)
        if stored is None:
            raise ValueError("that was already entered")
        return stored

    def compare_quotes(self, plan_id: str, closed: bool = False) -> quote_compare.Comparison:
        """Fully confirmed quotes ranked by a rule fixed in advance (``quote_compare.RULE``): order-blind, and withheld until enough vendors have answered or the user says they are done waiting."""
        return quote_compare.compare(self.quote_assessments(plan_id), self._scope(plan_id).request.guest_count, closed, chosen=self.quoted.message_ids())

    def confirm_quote_fields(self, plan_id: str, message_id: str, items: dict[str, str | None], by: str, valid_days: str | None = None) -> QuoteAssessment:
        """The user confirms fields of a reply: each as read (value None) or with the value they typed, which must read safely. An amount is confirmed on its own, never in a batch. Nothing is saved unless
        every field passes. Once the price and its validity are both confirmed the price is recorded as a quote, with the guests and date it was for."""
        scope = self._scope(plan_id)
        by = (by or "").strip()
        if not 1 <= len(by) <= 60:
            raise ValueError("say who confirmed it (a name)")
        msg = self._reply(plan_id, message_id)
        reading = quote_template.read_message(msg, msg.category)
        items = dict(items)
        if valid_days not in (None, "") and "valid_days" not in items:
            items["valid_days"] = str(valid_days)
        if not items:
            raise ValueError("nothing to confirm")
        planned = []
        for key, typed in items.items():
            if key not in quote_template.ASKED[msg.category]:
                raise ValueError(f"{key} is not asked of a {msg.category.value}")
            label, kind = quote_template.FIELDS[key]
            r = reading.fields[key]
            if typed in (None, ""):
                if r.state != "read":
                    raise ValueError(f"{label}: nothing was read safely from the reply; type the value")
                planned.append((key, r.value, False))
            else:
                value = parse_typed(msg.category, key, typed)
                planned.append((key, value, value != r.value))
        money = [k for k, _, _ in planned if quote_template.FIELDS[k][1] == "money"]
        if money and (len(money) > 1 or any(k not in money and k != "valid_days" for k, _, _ in planned)):  # a price may travel with how long it holds, nothing else
            raise ValueError("an amount is confirmed on its own: confirm the price and the deposit one at a time")
        for key, value, edited in planned:
            self.quote_confirmations.add(plan_id, message_id, msg.vendor_id, key, value, by, edited)
        self._record_confirmed_quote(plan_id, msg, scope, by)
        return next(a for a in self.quote_assessments(plan_id) if a.message_id == message_id)

    def retract_quote_field(self, plan_id: str, message_id: str, field: str, by: str) -> QuoteAssessment:
        """The user takes a confirmation back. A price already recorded as a quote is not taken back this way: confirm the right value instead and the newer quote stands."""
        self._scope(plan_id)
        msg = self._reply(plan_id, message_id)
        if field not in self.quote_confirmations.latest(plan_id, message_id):
            raise ValueError("that field is not confirmed")
        price_key = quote_template.PRICE_KEY[msg.category]
        if field in (price_key, "valid_days") and any(o.evidence_ref.startswith(f"reply {message_id}:") for o in self.observations.quotes(msg.vendor_id)):
            raise ValueError("this price is already recorded as a quote: confirm the right value instead, and the newer quote stands")
        if not (by or "").strip():
            raise ValueError("say who took it back (a name)")
        self.quote_confirmations.add(plan_id, message_id, msg.vendor_id, field, None, by, retracted=True)
        return next(a for a in self.quote_assessments(plan_id) if a.message_id == message_id)

    def _record_confirmed_quote(self, plan_id: str, msg: InboundMessage, scope, by: str) -> None:
        """When the price and how long it holds are both confirmed, record the price as a quote observation (once per confirmed amount). It carries the guests and the date it was for, so it can later
        be shown as out of date for another event."""
        got = self.quote_confirmations.latest(plan_id, msg.message_id)
        key = quote_template.PRICE_KEY[msg.category]
        if key not in got or "valid_days" not in got:
            return
        amount, days = int(got[key].value), int(got["valid_days"].value)
        tag = f"reply {msg.message_id}: {amount}"
        if any(o.evidence_ref == tag and o.expires_at == (msg.received_at.date() + timedelta(days=days)) for o in self.observations.quotes(msg.vendor_id)):
            return
        taxes = got.get("taxes_included")
        self.observe_quote(
            msg.vendor_id, msg.category, source_type=ObservationSource.VENDOR_QUOTE, entered_by=by, observed_at=msg.received_at, expires_at=msg.received_at.date() + timedelta(days=days), confidence=CONFIRM_CONFIDENCE,
            evidence_ref=tag, amount_inr=amount, basis="per_plate" if msg.category == VendorCategory.CATERER else "total", guest_count=scope.request.guest_count if msg.category == VendorCategory.CATERER else None, quoted_for_date=scope.request.event_date,
            hidden_costs=[HiddenCost(label="Taxes (GST) extra: ask for the amount")] if taxes is not None and taxes.value == "no" else [],
            note=f"from the vendor's reply of {msg.received_at.date().isoformat()}; the price and its validity were confirmed by {by}"[:200],
        )
        if self.shared_quotes is not None and msg.pasted_by is None and (keys := self._shared_keys(msg.vendor_id)):  # only what a vendor sent through the app is shared, never what a user typed in
            self.shared_quotes.add(
                keys, msg.category, amount, "per_plate" if msg.category == VendorCategory.CATERER else "total", scope.request.guest_count if msg.category == VendorCategory.CATERER else None,
                scope.request.event_date, msg.received_at, msg.received_at.date() + timedelta(days=days), None if taxes is None else taxes.value == "yes",
            )

    def _shared_keys(self, vendor_id: str) -> tuple[str, ...]:
        """How another person's workspace would recognise this vendor. A test vendor is never shared, and a seed vendor already has a price everyone sees."""
        biz = self.externals.get(vendor_id) if VendorRef.of(vendor_id).kind == "external" else None
        if biz is None or biz.is_test:
            return ()
        k = self.outreach_store.contacts().get(vendor_id)
        return shared_quotes.vendor_keys(biz.source, biz.external_id, biz.name, biz.city, biz.category, k.email if k else None, k.whatsapp if k else None)

    def shared_earlier(self, vendor_id: str, today: date, guests: int | None = None, event_date: date | None = None) -> list[dict]:
        """What other people were last quoted by this vendor, newest first, each with why it may not hold for this event. For orientation while this user's own request is out: never priced from."""
        biz = self.externals.get(vendor_id) if VendorRef.of(vendor_id).kind == "external" else None
        if self.shared_quotes is None or biz is None or not (keys := self._shared_keys(vendor_id)):
            return []
        mine = {(o.amount_inr, o.observed_at.date()) for o in self.observations.quotes(vendor_id)}  # this user's own quote is already on the card
        return [
            {"amount_inr": q.amount_inr, "basis": q.basis, "guest_count": q.guest_count, "observed_at": q.observed_at.isoformat(),
             "expires_at": q.expires_at.isoformat(), "taxes_included": q.taxes_included, "old": shared_quotes.reasons(q, today, guests, event_date)}  # the other person's event date never leaves the store
            for q in reversed(self.shared_quotes.earlier(keys, biz.category)) if (q.amount_inr, q.observed_at.date()) not in mine
        ]

    def place_details(self, place_id: str, phone: bool = False):
        """One Google listing as it is now, for a card. Nothing is stored."""
        return self.places.details(place_id, phone)

    def vendor_cards(self, plan_id: str, today: date | None = None, category: VendorCategory | None = None) -> list[VendorCard]:
        """A card for each shortlisted business: what the app holds, with where each fact came from and whether the user confirmed it. A Google listing's own fields are not here; ``place_id`` fetches them."""
        today = today or date.today()
        scope = self._scope(plan_id)
        drafts, replies = self.outreach_store.list(plan_id), self.outreach_store.inbound(plan_id)
        guests = getattr(getattr(scope, "request", None), "guest_count", None)
        when = getattr(getattr(scope, "request", None), "event_date", None)
        return [
            cards.assemble(i, self.observations.quotes(i.business.vendor_id), [d for d in drafts if d.vendor_id == i.business.vendor_id], [m for m in replies if m.vendor_id == i.business.vendor_id], today, guests, when,
                           self.shared_earlier(i.business.vendor_id, today, guests, when), self.contact_log.for_vendor(plan_id, i.business.vendor_id))
            for i in self.shortlist_view(plan_id, category)
        ]

    def request_text(self, plan_id: str, category: VendorCategory, vendor_name: str, sender_name: str) -> tuple[str, str]:
        """The fixed quote request, as subject and body, for a person to send themselves from their own mail or WhatsApp. It is built from the brief's facts alone (never the budget) and nothing is stored or sent."""
        scope = self._scope(plan_id)

        def said(raw: str, fallback: str) -> str:
            try:
                return outreach_mod._clean_name(raw) or fallback
            except ValueError:  # blank or not a plain name: the user fills it in before sending, nothing here is stored
                return fallback

        return outreach_mod._message(scope, category, said(vendor_name, "there"), said(sender_name, "[your name]"), self.outreach_store.checklists.get(category.value, ()))

    def _external(self, vendor_id: str) -> ExternalBusiness:
        biz = self.externals.get(vendor_id) if VendorRef.of(vendor_id).kind == "external" else None
        if biz is None:
            raise ValueError(f"unknown discovered business {vendor_id!r}")
        return biz

    def set_business_website(self, vendor_id: str, url: str) -> ExternalBusiness:
        """The business's own website, typed by the user when its listing has none. Its pages are the only place contacts are read from."""
        biz = self._external(vendor_id)
        if biz.is_test:
            raise ValueError("a test vendor has no website to read")
        return self.externals.set_website(vendor_id, (url or "").strip())

    def find_business_contacts(self, vendor_id: str) -> ContactFindResult:
        """Read the business's own website (and one contact page on the same domain) for public email addresses. Found addresses are candidates only."""
        biz = self._external(vendor_id)
        if biz.is_test:
            raise ValueError("a test vendor's address is already confirmed by its owner; there is no website to read")
        website = biz.website
        if biz.source == "gplaces" and not website:  # the listing's website is not stored: ask Google for it now, use it, keep nothing of it
            website = self.places.details(biz.external_id.split(":", 1)[1]).website
        if not website:
            raise ValueError(f"{biz.name} has no website: type its website first (its listing did not publish one)")
        started = time.perf_counter()
        res = find_contacts(self.contact_fetcher(), website)
        added = self.shortlist.add_candidates(vendor_id, res.found)
        self.logs.tool_calls.log(
            request_id="sourcing", tool="contacts.find", args={"vendor_id": vendor_id, "pages": len(res.pages)}, ok=bool(res.pages),
            result=f"{len(res.found)} found, {len(added)} new", duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return ContactFindResult(vendor_id=vendor_id, added=tuple(added), pages=tuple(res.pages), notes=tuple(res.notes))

    def reject_contact(self, vendor_id: str, candidate_id: str):
        """The user says this address is not the business's. It will not be offered again."""
        self._external(vendor_id)
        cand = self.shortlist.get_candidate(candidate_id)
        if cand is None or cand.vendor_id != vendor_id:
            raise ValueError("unknown business or contact candidate")
        if cand.status != "found":
            raise ValueError(f"that address is already {cand.status}")
        return self.shortlist.update(candidate_id, status="rejected")

    def confirm_contact(self, vendor_id: str, candidate_id: str, by: str, edited_value: str | None = None, acknowledge_bounce: bool = False, business_name: str | None = None) -> VendorContact:
        """The user confirms an address that was found for a discovered business, optionally correcting it. Only this creates its contact."""
        biz = self.externals.get(vendor_id)
        cand = self.shortlist.get_candidate(candidate_id)
        if biz is None or cand is None or cand.vendor_id != vendor_id:
            raise ValueError("unknown business or contact candidate")
        if cand.status != "found":
            raise ValueError(f"that address is already {cand.status}")
        if not (by or "").strip():
            raise ValueError("say who confirmed it (a name)")
        if biz.source == "gplaces" and biz.name_source != "user_confirmed" and not (business_name or "").strip():
            raise ValueError("confirm the business name: type it as you want it in your own record (a Google listing's name is not stored)")
        value = clean_email(edited_value) if edited_value else cand.value
        edited = value.casefold() != cand.value.casefold()
        earlier = self.suppressions.active_for("email", value)
        if earlier is not None and earlier.reason == "bad_address" and acknowledge_bounce is not True:
            raise NeedsAcknowledgement(earlier.detail)  # nothing is saved until the user has seen why this address failed
        if biz.source == "gplaces" and (business_name or "").strip():
            self.externals.confirm_name(vendor_id, business_name)
        now = self.outreach_store.now()
        contact = self.outreach_store.set_contact_record(VendorContact(
            vendor_id=vendor_id, email=value, preferred="email", updated_at=now, origin="discovered", source_url=cand.source_url,
            confirmed_by=by.strip(), confirmed_at=now, edited_by_user=edited))
        self.evidence.record(vendor_id, "email", value, FactState.USER_CONFIRMED, "user_input", cand.source_url)
        self.shortlist.update(candidate_id, status="confirmed", confirmed_by=by.strip(), confirmed_at=now, confirmed_value=value if edited else None)
        self.suppressions.lift_bad_address("email", value, by.strip(), "address re-confirmed by the user", acknowledge_bounce)  # a bad address is cleared by editing or re-confirming; a stop request never is
        return contact

    # ------------------------------------------------------------ do not contact

    def is_suppressed(self, channel: str, value: str):
        return self.suppressions.active_for(channel, value)

    def suppress_address(self, channel: str, value: str, by: str, note: str = ""):
        """The user marks an address do-not-contact. It stops every unsent draft to it, on every plan."""
        if not (by or "").strip():
            raise ValueError("say who is adding it (a name)")
        s = self.suppressions.add(channel, value, "user", (note or "").strip(), by.strip())
        self.conversations.apply_block(channel, value)
        return s

    def lift_suppression(self, suppression_id: str, by: str, reason: str, acknowledge_reason: bool = False):
        """Only the user lifts one, with a recorded reason (and, for an address that bounced, having read why). Drafts that were blocked stay blocked: write a new one."""
        return self.suppressions.lift(suppression_id, by, reason, acknowledge_reason)

    def suppression_list(self):
        return [(s, domain_note(s.channel, s.value)) for s in self.suppressions.all()]

    def unmatched_messages(self, include_reviewed: bool = False):
        return [m for m in self.unmatched.all() if include_reviewed or not m.reviewed]

    def dismiss_unmatched(self, message_id: str):
        return self.unmatched.dismiss(message_id)

    def suppress_unmatched(self, message_id: str, by: str):
        """The user reviewed an unmatched message and decided to stop contacting that sender. Their decision, not the message's."""
        m = self.unmatched.get(message_id)
        if m is None:
            raise ValueError(f"unknown message {message_id!r}")
        s = self.suppress_address(m.channel, m.sender, by, f"from an unmatched message ({m.kind})")
        self.unmatched.dismiss(message_id)
        return s

    def vendor_contacts(self):
        return list(self.outreach_store.contacts().values())

    def find_places(self, kind: str, city: str, reviews: bool = False, limit: int = 10):
        """Google ratings, review counts and price level for a service in a city."""
        return self.places.search(kind, city, reviews, limit)

    def find_deals(self, query):
        """Search the web for advertised prices and rank the best of the best. Leads only; every search and fetch is logged."""
        rid = "deals-" + uuid.uuid4().hex[:16]

        def log(tool, args, ok, text, ms):
            safe = {k: (v[:300] if isinstance(v, str) else v) for k, v in args.items()}
            self.logs.tool_calls.log(request_id=rid, tool=tool, args=safe, ok=ok, result=text[:2000], duration_ms=ms)

        return self.deals_factory(log).run(query)

    def search_flights(self, origin: str, destination: str, day: date, **kw):
        """Indicative fares and schedules for one direction. Nothing is booked."""
        check_search(origin, destination, kw.get("adults", 1), kw.get("cabin", "economy"), kw.get("limit", 10))  # whatever the provider is
        return self.flights.search_one_way(origin, destination, day, **kw)

    def cheapest_flight_dates(self, origin: str, destination: str, start: date, end: date, today: date | None = None, **kw):
        """The lowest indicative fare for each day in a range (at most 61 days), one direction. Nothing is booked."""
        check_search(origin, destination, kw.get("adults", 1), kw.get("cabin", "economy"), 1)
        check_range(start, end, today or date.today())
        return self.flights.cheapest_dates(origin, destination, start, end, **kw)

    def plan_trip(self, request, today: date | None = None, **kw):
        """Order the stops, choose dates, search every distinct leg and rank complete itineraries. Every search is logged."""
        rid = "trip-" + uuid.uuid4().hex[:16]

        def log(leg, ok, text, ms):
            self.logs.tool_calls.log(
                request_id=rid, tool="flights.search_one_way", ok=ok, result=text, duration_ms=ms,
                args={"origin": leg[0], "destination": leg[1], "date": leg[2].isoformat(), "adults": request.adults, "cabin": request.cabin},
            )

        return search_trip(self.flights, request, today or date.today(), on_call=log, **kw)

    def search_multi_city(self, legs, **kw):
        return search_multi_city(self.flights, legs, **kw)

    def confirm(self, plan_id: str, category: VendorCategory) -> ReplanOutcome:
        outcome = self.replanner.confirm(plan_id, category)
        self._after_new_version(plan_id)
        return outcome

    def replan(self, plan_id: str, disruption: Disruption, today: date | None = None) -> ReplanOutcome:
        self._clock(today)
        if disruption.kind == DisruptionKind.QUOTE_UPDATE and not any(r.price_stale for r in self.quote_status(plan_id, today)):
            v = ConstraintViolation(constraint="nothing_to_reprice", detail="no vendor in this plan costs anything different from what the plan priced", repair="confirm a quote first")
            return ReplanOutcome(status="failed", violations=(v,))
        outcome = self.replanner.replan(plan_id, disruption, today)
        self._after_new_version(plan_id)
        return outcome

    def change_turn(self, plan_id: str, message: str, today: date | None = None):
        """The user's words about a change, as a delta to confirm and the questions still open. Nothing is changed."""
        from eventmate.planning import delta as change

        return change.formalize_delta(message, self.store.latest(plan_id), today or date.today(), self.llm, logs=self.logs)

    def apply_change(self, plan_id: str, delta, done=(), in_flight=None, today: date | None = None) -> ReplanOutcome:
        """Apply a delta the user confirmed. ``done`` are tasks the user says are finished and ``in_flight`` maps tasks under way to the minutes they have left;
        the times are taken from the plan and from today, never from the caller."""
        from eventmate.planning.exec_state import ExecState

        today = today or date.today()
        parent = self.store.latest(plan_id)
        now = self.replanner.now_offset(parent, today)
        ends = {i.task_id: int((i.end - parent.origin).total_seconds() // 60) for i in parent.items}
        state = ExecState(now=now, done={t: min(ends.get(t, 0), now) for t in done}, in_flight=dict(in_flight or {}))
        return self.repair(plan_id, delta, state, today)

    def repair(self, plan_id: str, delta, state, today: date | None = None) -> ReplanOutcome:
        """Apply a confirmed change delta over the execution state as one new version (architecture L5)."""
        self._clock(today)
        outcome = self.replanner.repair(plan_id, delta, state, today)
        self._after_new_version(plan_id)
        return outcome

    def _after_new_version(self, plan_id: str) -> None:
        """A new plan version may make waiting vendor drafts untrue: mark those stale, and let the quote jobs that own them say so. A no-op when nothing changed."""
        if self.conversations.supersede_stale(plan_id) and (self.store.root / "jobs.db").exists():
            self.jobs.reevaluate(plan_id)

    def update_community(self, plan_id: str, details, today: date | None = None) -> ReplanOutcome:
        """Replace a community plan's details (attendance, funding, volunteers, programme, access needs) as a validated new version, or refuse with repairs."""
        return self.replan(plan_id, Disruption(kind=DisruptionKind.COMMUNITY_UPDATE, new_community=details), today)

    def record_venue_accessibility(self, venue_id: str, features, evidence_ref: str, entered_by: str):
        """What a venue told the user about its access. Needs evidence; it turns an unverified item into a pass or a failure on the next validation."""
        if self.repo.get(VendorCategory.VENUE, venue_id) is None:
            raise ValueError(f"{venue_id!r} is not a venue in the database")
        return self.access_store.record(venue_id, features, evidence_ref, entered_by)

    def explain(self, plan_id: str, version: int | None = None, with_diff: bool = True) -> Explanation:
        plan = self.store.load(plan_id, version)
        diff = None
        if with_diff and plan.parent_version:
            diff = diff_plans(self.store.load(plan_id, plan.parent_version), plan)
        comm = community_mod.build_status(plan, self.checklist_store, self.revalidate(plan_id, version)) if plan.request.event_type.value == "community" else None
        return self.explainer.explain(plan, diff, comm, self.verification(plan_id, version))

    def community_status(self, plan_id: str, today: date | None = None):
        """The community view of a plan: hard checks, what could not be decided, and the conditional items with the user's marks."""
        plan = self.store.latest(plan_id)
        report = self.revalidate(plan_id) if plan.request.event_type.value == "community" else None  # current data: a venue's recorded answer counts at once
        return community_mod.build_status(plan, self.checklist_store, report)

    def set_community_item(self, plan_id: str, item_id: str, status: str, by, on, note: str = "", today: date | None = None):
        """The user's mark on one conditional item (confirmed, not applicable, or reopened). Nothing else ever changes an item."""
        plan = self.store.latest(plan_id)
        if plan.request.event_type.value != "community":
            raise ValueError("this plan is not a community event")
        shown = [d.item_id for d, _ in community_mod.shown_items(plan.request)]
        return self.checklist_store.mark(plan_id, item_id, shown, status, by, on, note, today or date.today())

    def diff(self, plan_id: str, old_version: int, new_version: int) -> PlanDiff:
        return diff_plans(self.store.load(plan_id, old_version), self.store.load(plan_id, new_version))

    def decisions_for(self, plan: PlanVersion, max_files: int = 40):
        """Decision-log entries recorded while this plan version was produced (any run's log file)."""
        wanted = set(plan.decision_entry_ids)
        found = []
        files = sorted(self.logs.decisions.log_dir.glob("decisions-*.jsonl*"), key=lambda p: p.stat().st_mtime, reverse=True)[:max_files]  # the active logs and the rotated ones
        for path in files:
            if path.stat().st_size > 20_000_000:
                continue
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if any(i in line for i in wanted):
                        entry = DecisionLogEntry.model_validate(migrations.parse_line("decisions", line, path.name, 0))
                        if entry.entry_id in wanted:
                            found.append(entry)
        return sorted(found, key=lambda e: e.timestamp)

    def revalidate(self, plan_id: str, version: int | None = None) -> ValidationReport:
        """Re-check a stored plan against the *current* database and rules."""
        plan = self.store.load(plan_id, version)
        parent = self.store.load(plan_id, plan.parent_version) if plan.parent_version else None
        return self.validator.validate(draft_from_plan(plan), parent, released_vendor_ids=plan.released_vendor_ids, newly_confirmed_vendor_ids=plan.newly_confirmed_vendor_ids)
