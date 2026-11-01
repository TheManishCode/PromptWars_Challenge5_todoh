"""The assistant: route, retrieve, call permitted tools, answer, then check the answer before anyone sees it.

The model is treated as untrusted at every step. Its tool requests are validated and permission-checked by code, tool and
document output reaches it only as fenced, PII-masked data, mutations become proposals, and its final answer must be backed by
the evidence gathered this turn or it is replaced by a deterministic one.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Sequence

from pydantic import Field

from eventmate.assistant import guard
from eventmate.assistant.annotations import Annotation, AnnotationStore
from eventmate.assistant.approvals import ApprovalError, ApprovalQueue, Proposal
from eventmate.assistant.cache import AnswerCache, cache_key
from eventmate.assistant.identity import Perm, Principal
from eventmate.assistant.knowledge import KnowledgeBase, build_default
from eventmate.assistant.langfuse import LangfuseExporter
from eventmate.assistant.router import Route, route
from eventmate import consumption
from eventmate.privacy import Placeholders
from eventmate.assistant.session import SessionStore, depends_on_context
from eventmate.assistant.tools import Tool, ToolContext, build_tools, execute, visible_tools
from eventmate.assistant.trace import FeedbackLog, ProposalLog, TraceBuilder, TraceLog, sha, tokens
from eventmate.env import redact_secrets
from eventmate.netutil import loads
from eventmate.untrusted import UntrustedTextRejected, normalize_untrusted, render_template
from eventmate.llm import LLMClient, PromptError, PromptStore, client_from_env
from eventmate.schemas import _Model
from eventmate.service import EventMate

PROMPT_ID = "assistant.system"
MAX_QUESTION = 1000
MAX_STEPS = 4
_MAX_TOKENS = 700
_JSON_OBJECT = re.compile(r"\{.*\}", re.S)
_ACTION_CLAIM = re.compile(r"\b(?:done|recorded|applied|saved|paid|confirmed|booked|replanned|updated|cancell?ed|completed|sent|refunded|approved|processed)\b", re.I)
NO_EVIDENCE = "I could not find that in this plan or in to;doh-the event planner's documents, so I won't guess."


class AnswerResult(_Model):
    answer: str
    citations: tuple[str, ...] = ()
    proposals: tuple[Proposal, ...] = ()
    route: str
    cache: str
    outcome: str  # answered | refused | degraded | cancelled (a voice turn dropped before it could commit anything)
    trace_id: str
    degraded_reason: str | None = Field(default=None, max_length=300)


class AssistantError(ValueError):
    pass


def prompt_sha(prompts: PromptStore | None = None) -> str:
    body, _ = (prompts or PromptStore()).load(PROMPT_ID)
    return sha(body)


class Assistant:
    def __init__(self, em: EventMate, llms: dict[str, LLMClient] | None = None, kb: KnowledgeBase | None = None, prompts: PromptStore | None = None,
                 cache: AnswerCache | None = None, policy: dict | None = None, exporter: LangfuseExporter | None = None, log_dir: Path | None = None, run_id: str | None = None):
        self.em = em
        self.llms = {k: v for k, v in (llms or {}).items() if v is not None}
        self.kb = kb or build_default(em.manager.budget.policy)
        self.prompts = prompts or PromptStore()
        self.cache = cache or AnswerCache()
        self.tools: dict[str, Tool] = build_tools()
        log_dir = log_dir or em.logs.decisions.log_dir
        run_id = run_id or em.logs.decisions.run_id
        self.traces = TraceLog(log_dir, run_id)
        self.queue = ApprovalQueue(ProposalLog(log_dir, run_id), db=Path(log_dir) / "assistant_approvals.db")  # durable: a proposal waiting for a click survives a restart
        self.prompt_body, self.prompt_version = self.prompts.load(PROMPT_ID)
        self.prompt_sha = sha(self.prompt_body)
        self.policy = policy or guard.load_policy()
        self.kb.min_score = self.policy["min_retrieval_score"]
        self.annotations = AnnotationStore(log_dir)
        self.feedback_log = FeedbackLog(log_dir, run_id)
        self.exporter = exporter if exporter is not None else LangfuseExporter.from_env()
        self.sessions = SessionStore()
        self._spend: dict[str, deque[tuple[float, int]]] = defaultdict(deque)  # user -> (time, est. tokens) in the last hour

    @classmethod
    def from_env(cls, em: EventMate, **kw) -> Assistant:
        """Small and large models from the environment; either falls back to the other, and to ``em.llm``."""
        large = client_from_env(tier="heavy") or em.llm
        small = client_from_env(tier="light") or large
        return cls(em, {"large": large, "small": small}, **kw)

    def forget_plan(self, plan_id: str) -> None:
        """Drop what the assistant remembers about a deleted plan: conversation memory, cached answers and approved annotations."""
        self.sessions.clear_plan(plan_id)
        self.cache.clear()
        self.annotations.drop_plan(plan_id)
        self.queue.forget_plan(plan_id)

    # ------------------------------------------------------------------ ask

    def ask(self, question: str, plan_id: str, principal: Principal, turn: Any | None = None) -> AnswerResult:
        """Answer one question. ``turn`` is given for a voice turn (eventmate.voice.turns.Turn): it stages proposals, never writes the answer cache, and is
        checked for cancellation after every blocking step, so a cancelled turn commits nothing. Typed chat passes no turn and behaves exactly as before."""
        if not isinstance(question, str) or not question.strip():
            raise AssistantError("ask a question")
        if len(question) > MAX_QUESTION:
            raise AssistantError(f"questions are limited to {MAX_QUESTION} characters")
        try:
            question = normalize_untrusted(question, MAX_QUESTION)  # screened, masked, cached, traced and sent on in this one normalised form
        except UntrustedTextRejected as exc:
            raise AssistantError(str(exc)) from None
        plan = self.em.store.latest(plan_id)  # unknown or hostile ids fail here
        roles = tuple(r.value for r in principal.roles)
        tb = TraceBuilder(plan_id, principal.user_id, roles, question)
        ph = Placeholders()
        model_question = ph.mask(question)  # what a model may see: contact details become placeholders
        flags = guard.scan(question)
        if flags:
            tb.step("guard", "injection_scan", ok=False, detail="question contains instruction-like text: " + ", ".join(flags))
        state_fp = f"{plan.version}:{len(self.em.ledger_entries(plan_id))}"
        if guard.input_blocked(question, self.policy):  # moderation: a preset reply, no tool or model runs
            tb.step("guard", "input_moderation", ok=False, detail="question matched the input block list")
            result = AnswerResult(answer=self.policy["preset_reply"], route="moderated", cache="skipped", outcome="refused", trace_id=tb.id)
            return self._record(tb, result, principal, plan_id, roles)
        note = self.annotations.match(question, plan_id, roles, state_fp)
        if note is not None:  # a human approved this answer for this plan state: reuse it, no model
            tb.step("route", "annotation", detail=f"approved answer {note.id} by {note.created_by}")
            result = AnswerResult(answer=note.answer, citations=note.citations, route="annotation", cache="skipped", outcome="answered", trace_id=tb.id)
            return self._record(tb, result, principal, plan_id, roles)
        r = route(question)
        tb.step("route", r.kind, detail=r.reason + (f" -> {r.tool}" if r.tool else ""))
        mem_user = turn.memory_user if turn is not None else principal.user_id  # a voice session has its own memory, never shared with typed chat or another session
        memory = "" if r.kind == "direct" else self.sessions.block(plan_id, mem_user)  # routine questions are answered by a tool alone: no model, so no memory
        key = cache_key(question + ("\n" + memory if depends_on_context(question) else ""), roles, plan_id, state_fp, self.kb.version, self.prompt_sha)  # an answer given with context is never reused without it

        def compute():
            result, evidence_hostile = self._answer(model_question, plan_id, principal, r, tb, memory, turn)
            return result, turn is None and result.outcome == "answered" and not result.proposals and not flags and not evidence_hostile

        result, cache_state = self.cache.get_or_compute(key, compute)
        if turn is not None and turn.cancelled():
            return AnswerResult(answer="", route=r.kind, cache="skipped", outcome="cancelled", trace_id=tb.id)  # nothing below runs: no trace, memory or proposal
        if cache_state != "miss":
            tb.step("cache", cache_state, detail="answer reused; no model or tool ran for this question")

        def commit(res: AnswerResult) -> AnswerResult:
            """Everything a finished answer writes: trace, memory, and (voice) the proposals it staged."""
            if turn is not None and res.proposals:
                try:
                    res = res.model_copy(update={"proposals": tuple(self.queue.adopt(p)[0] for p in res.proposals)})
                except ApprovalError as exc:
                    res = AnswerResult(answer=str(exc), route=r.kind, cache="skipped", outcome="refused", trace_id=tb.id)
            final = self._record(tb, res.model_copy(update={"cache": cache_state}), principal, plan_id, roles, question)
            self.sessions.record(plan_id, mem_user, question, final.answer, final.outcome)
            return final

        if turn is None:
            final = commit(result)
        else:
            final = turn.commit(lambda: commit(result))  # the barrier: a cancel that got there first wins, and then none of the above has run
            if final is None:
                return AnswerResult(answer="", route=r.kind, cache="skipped", outcome="cancelled", trace_id=tb.id)
        return final.model_copy(update={"answer": ph.restore(final.answer)})  # real values back only in what this person sees; the trace and memory hold the masked text

    def _record(self, tb: TraceBuilder, result: AnswerResult, principal: Principal, plan_id: str, roles: tuple[str, ...], question: str | None = None) -> AnswerResult:
        question = tb.question if question is None else question
        result = result.model_copy(update={"trace_id": tb.id})
        try:
            entry = self._write_trace(tb, result, principal, plan_id, roles, question)
        except OSError as exc:  # a full or failing disk must not stop the person from being answered: the trace is lost, the answer is not
            logging.getLogger("eventmate.assistant").error("trace for %s not written: %s", tb.id, type(exc).__name__)
            return result
        if self.exporter:
            self.exporter.export_trace(entry)
        return result

    def _write_trace(self, tb: TraceBuilder, result: AnswerResult, principal: Principal, plan_id: str, roles: tuple[str, ...], question: str):
        return self.traces.write(
            request_id=tb.id, plan_id=plan_id, user_id=principal.user_id, roles=roles, question_sha256=sha(question), question_masked=guard.mask_pii(question)[:240],
            answer_masked=guard.mask_pii(result.answer)[:600], route=result.route, cache=result.cache, outcome=result.outcome, steps=tuple(tb.steps), cited=result.citations,
            models=tuple(dict.fromkeys(tb.models)), prompt_sha256=self.prompt_sha, kb_version=self.kb.version, tokens_in_est=tb.tin, tokens_out_est=tb.tout,
            total_ms=round((time.perf_counter() - tb.t0) * 1000, 2),
        )

    # ------------------------------------------- curation and feedback (humans)

    def _own_trace(self, trace_id: str, principal: Principal, need: Perm | None = None):
        t = self.traces.find(trace_id)
        if t is None or (t.user_id != principal.user_id and not principal.can(Perm.TRACE_READ)) or (need and not principal.can(need)):
            raise AssistantError("not allowed or unknown answer")
        return t

    def approve_answer(self, trace_id: str, principal: Principal, answer: str | None = None) -> Annotation:
        """An approver turns a real answer into a reusable approved answer (optionally rewording it first)."""
        t = self._own_trace(trace_id, principal, Perm.APPROVE)
        if t.outcome != "answered" and answer is None:
            raise AssistantError("only a successful answer can be approved as it is; reword it first")
        plan = self.em.store.latest(t.plan_id)
        return self.annotations.add(plan_id=t.plan_id, roles=t.roles, question=t.question_masked, answer=answer or t.answer_masked, citations=t.cited,
                                    state=f"{plan.version}:{len(self.em.ledger_entries(t.plan_id))}", created_by=principal.user_id)

    def give_feedback(self, trace_id: str, principal: Principal, value: int, comment: str = ""):
        """A thumbs up or down from the person who asked (or an organiser); kept locally and sent as a Langfuse score."""
        self._own_trace(trace_id, principal)
        if value not in (-1, 1) or len(comment) > 200 or "<" in comment or ">" in comment:
            raise AssistantError("feedback must be 1 or -1 with a short plain comment")
        f = self.feedback_log.write(request_id=trace_id, user_id=principal.user_id, value=value, comment=guard.mask_pii(comment))
        if self.exporter:
            self.exporter.export_score(f)
        return f

    # ------------------------------------------------------ cost and reliability

    def _over_budget(self, user_id: str) -> bool:
        q, now = self._spend[user_id], time.monotonic()
        while q and now - q[0][0] > 3600:
            q.popleft()
        return sum(t for _, t in q) >= self.policy["token_cap"]

    def _complete(self, kind: str, prompt: str, user_id: str, tb: TraceBuilder) -> tuple[str, str, str]:
        """Retry with backoff, then fall back to the other model tier (the gateway pattern). Returns (reply, model name, tier that answered)."""
        tiers = [k for k in dict.fromkeys((kind, "large", "small")) if k in self.llms]
        last: Exception | None = None
        for tier in tiers:
            client = self.llms[tier]
            for attempt in range(self.policy["retries"] + 1):
                try:
                    reply = client.complete("You are a careful assistant. Reply with one JSON object only.", prompt, _MAX_TOKENS)
                    self._spend[user_id].append((time.monotonic(), tokens(prompt) + tokens(reply)))
                    return reply, getattr(client, "name", "unknown"), tier
                except consumption.ConsumptionError:
                    raise  # a limit is not a provider fault: no retry, no other tier
                except Exception as exc:  # provider error, timeout or rate limit
                    last = exc
                    tb.step("llm", getattr(client, "name", "unknown"), ok=False, detail=f"attempt {attempt + 1} failed: {type(exc).__name__}")
                    time.sleep(self.policy["backoff"] * (2 ** attempt))
        raise last or RuntimeError("no model available")

    def decide(self, proposal_id: str, principal: Principal, approve: bool) -> Proposal:
        """A human's decision on a proposal. The only path by which an assistant suggestion changes anything."""
        p = self.queue.get(proposal_id)
        tool = self.tools[p.tool]

        def apply(prop: Proposal) -> str:
            return tool.apply(ToolContext(self.em, principal, prop.plan_id, self.kb), prop.args)

        return self.queue.decide(proposal_id, principal, approve, lambda pid: self.em.store.latest(pid).version, apply, tool.perm)

    # --------------------------------------------------------------- answering

    def _answer(self, question: str, plan_id: str, principal: Principal, r: Route, tb: TraceBuilder, memory: str = "", turn: Any | None = None) -> tuple[AnswerResult, bool]:
        ctx = ToolContext(self.em, principal, plan_id, self.kb, turn.staging if turn is not None else self.queue)
        evidence: list[str] = []
        cited: dict[str, str] = {}  # id -> text that may be cited
        proposals: list[Proposal] = []
        hostile = [False]

        def call(name: str, args: Any) -> str:
            started = time.perf_counter()
            res = execute(ctx, self.tools, name, args)
            tb.step("tool", name, ok=res.ok, detail=json.dumps(args, default=str)[:200] + ("" if res.ok else " -> " + res.error), started=started)
            if res.proposal:
                proposals.append(res.proposal)
                tb.step("policy", "proposal_filed", detail=f"{res.proposal.id}: {res.proposal.summary}; waiting for human approval")
            text = guard.mask_pii(res.text())
            hits = guard.scan(text)
            if hits:
                hostile[0] = True
                tb.step("guard", "injection_scan", ok=False, detail=f"data returned by {name} contained instruction-like text ({', '.join(hits)}); it is data only")
            if res.ok:
                evidence.append(text)
                cited[name] = text
                for p in res.data.get("passages", []):
                    cited[p["id"]] = p["text"]
            return text

        def done(answer: str, citations: tuple[str, ...], outcome: str, reason: str | None = None) -> tuple[AnswerResult, bool]:
            tb.step("output", outcome, ok=outcome == "answered", detail=reason or "")
            answer = guard.mask_pii(answer)
            return AnswerResult(answer=answer, citations=citations, proposals=tuple(proposals), route=r.kind, cache="miss", outcome=outcome, trace_id=tb.id, degraded_reason=reason), hostile[0]

        if r.kind == "direct":
            text = call(r.tool, {"query": question} if r.tool == "search_knowledge" else {})
            if turn is not None and turn.cancelled():
                return done("", (), "cancelled")
            data = json.loads(text)
            if "error" in data:
                return done("You don't have access to that information for this event.", (), "refused", data["error"])
            return done(_render(r.tool, data), (r.tool,), "answered")

        def fallback(reason: str, outcome: str = "degraded") -> tuple[AnswerResult, bool]:
            lines = [x for x in (_readable(t) for t in evidence[-2:]) if x]
            if lines:  # the tool results in words, never as raw JSON (an outside verifier saw '{"passages": [...]}' shown to a user)
                return done("Here is what I found:\n" + "\n".join(f"- {x}" for x in lines), tuple(cited), outcome, reason)
            return done(NO_EVIDENCE, (), "refused", reason)  # nothing readable was found (an empty search, say): the honest answer, not an empty list

        if not self.llms or self._over_budget(principal.user_id):  # no model, or this user's hourly allowance is spent: still useful, never invented
            text = call("search_knowledge", {"query": question})
            passages = json.loads(text).get("passages", [])
            why = "no AI model is configured" if not self.llms else "the hourly AI usage limit for this account is reached"
            if not passages:
                return done(NO_EVIDENCE, (), "refused", why + " and no document matched")
            return done(_render("search_knowledge", json.loads(text)), (passages[0]["id"],), "degraded", why + "; showing the closest document passage")

        try:
            body, _ = self.prompts.load(PROMPT_ID)
        except PromptError as exc:
            return done(NO_EVIDENCE, (), "degraded", f"prompt unavailable: {exc}")
        catalogue = "\n".join(f"- {t.name}: {t.description} Arguments: {json.dumps(t.args.model_json_schema().get('properties', {}))}" for t in visible_tools(self.tools, principal))
        transcript = (guard.fence("EARLIER CONVERSATION", memory) + "\n\n" if memory else "") + guard.fence("QUESTION", question)
        reasked, tier = False, r.kind
        for step in range(MAX_STEPS):
            if turn is not None and turn.cancelled():  # checked again after each blocking step (a model call cannot be aborted; its result is dropped here)
                return done("", (), "cancelled")
            prompt = render_template(body, {"tools": catalogue, "transcript": transcript})  # one pass: a value containing "{tools}" stays literal
            started = time.perf_counter()
            try:
                reply, model, tier = self._complete(tier, prompt, principal.user_id, tb)  # stay on the tier that last worked
                tb.llm(model, prompt, reply)
                tb.step("llm", model, detail=f"step {step + 1}", started=started)
                found = _JSON_OBJECT.search(reply)
                msg = loads(found.group(0)) if found else None
                if not isinstance(msg, dict):
                    raise ValueError("reply was not a JSON object")
            except consumption.ConsumptionError as exc:
                if exc.code == "workspace_busy":
                    raise  # the web layer answers 429 with the code; nothing was spent
                tb.step("llm", "allowance", ok=False, detail=f"{exc.code}: {exc}"[:300])
                return fallback(f"the AI usage allowance is reached ({exc.code})")
            except Exception as exc:
                tb.step("llm", "unusable_reply", ok=False, detail=redact_secrets(f"{type(exc).__name__}: {exc}")[:300])
                return fallback(f"model reply unusable: {type(exc).__name__}"[:300])
            if "tool" in msg and "answer" not in msg:
                obs = call(msg.get("tool"), msg.get("args"))
                transcript += f"\n\nYou called {str(msg.get('tool'))[:60]}. Result:\n" + guard.fence("DATA tool:" + str(msg.get("tool"))[:60], obs)
                continue
            if set(msg) - {"answer", "citations"} or not isinstance(msg.get("answer"), str):
                return fallback("reply had unexpected fields")
            problem, valid = self._verify(msg, question, evidence, cited, proposals)
            if problem is None:
                return done(_with_pending_notice(msg["answer"].strip(), proposals), valid, "answered")
            if not reasked and step + 1 < MAX_STEPS:  # one re-ask with the reason (guardrails "reask"), then give up safely
                reasked = True
                tb.step("policy", "reask", ok=False, detail=problem)
                transcript += f"\n\nYour last reply was rejected: {problem}. Use a tool if you have no evidence yet, then answer again using only the data above."
                continue
            return fallback(problem, "degraded" if evidence else "refused")
        return fallback("no final answer within the step limit")

    def _verify(self, msg: dict[str, Any], question: str, evidence: list[str], cited: dict[str, str], proposals: Sequence[Proposal] = ()) -> tuple[str | None, tuple[str, ...]]:
        """The output guard: None if the answer may be shown, else why not."""
        answer = msg["answer"].strip()
        if proposals and _ACTION_CLAIM.search(answer):  # a proposal changes nothing until a human approves it, so no answer may say it was done
            return "this turn only filed a proposal for approval; do not say anything was done, recorded or confirmed", ()
        citations = tuple(c for c in msg.get("citations", []) if isinstance(c, str))
        if not evidence:
            return "you answered without using any tool or document", ()
        valid = tuple(c for c in citations if c in cited)
        if not valid or len(valid) != len(citations):
            return "citations were missing or not from this conversation", ()
        joined = " ".join(evidence)
        names = guard.introduced_names(answer, joined, question)
        problem = guard.check_answer(answer, joined, question) or guard.policy_violation(answer, self.policy) or (f"names not in the evidence: {names[:5]}" if names else None)
        return problem, valid


_PLAIN_KEY = re.compile(r"_+")


def _readable(text: str, limit: int = 300) -> str:
    """A tool result as plain words, or "" when it holds nothing to say. Passages become "Title: text"; anything else becomes "key: value" pairs. No braces, quotes or
    JSON syntax reach the user, and nothing is added that the result did not hold."""
    try:
        data = json.loads(text)
    except ValueError:
        return text.strip()[:limit]
    if isinstance(data, dict) and "error" in data and len(data) == 1:
        return ""

    def words(v: Any, depth: int = 0) -> str:
        if isinstance(v, dict):
            parts = [f"{_PLAIN_KEY.sub(' ', str(k)).strip()}: {words(x, depth + 1)}" for k, x in v.items() if x not in (None, "", [], {})]
            return "; ".join(p for p in parts if not p.endswith(": ")) if depth < 3 else ""
        if isinstance(v, (list, tuple)):
            items = [words(x, depth + 1) for x in v[:6]]
            return ", ".join(i for i in items if i) + (f" and {len(v) - 6} more" if len(v) > 6 else "")
        if isinstance(v, bool):
            return "yes" if v else "no"
        return str(v)

    if isinstance(data, dict) and isinstance(data.get("passages"), list):
        out = [f"{p.get('title', '').strip()}: {p.get('text', '').strip()}".strip(": ") for p in data["passages"][:2] if isinstance(p, dict)]
        return " ".join(o for o in out if o)[:limit]
    return words(data)[:limit]


def _with_pending_notice(answer: str, proposals: Sequence[Proposal]) -> str:
    """Code-written line after an answer that filed proposals, so the state is stated by code and not only by model prose."""
    if not proposals:
        return answer
    return answer + "\n" + " ".join(f"Waiting for your approval (nothing has changed yet): {p.summary}." for p in proposals)


def _inr(n: Any) -> str:
    return f"INR {int(n):,}"


def _render(tool: str, d: dict[str, Any]) -> str:
    """Deterministic wording for routine questions: no model, so nothing can be invented."""
    if tool == "get_public_event_info":
        venue = f" at {d['venue']}" if d.get("venue") else ""
        kind = d["event_type"].replace("_", " ")
        return f"It is {'an' if kind[0] in 'aeiou' else 'a'} {kind} in {d['city']} on {d['event_date']}{venue}."
    if tool == "get_budget_status":
        over = "over" if d["headroom"] < 0 else "under"
        return (f"Budget {_inr(d['budget_total'])}. Planned vendors cost {_inr(d['planned_total'])}. Committed {_inr(d['committed_total'])}, paid {_inr(d['paid_total'])}, "
                f"{_inr(abs(d['headroom']))} {over} budget (as recorded by you; to;doh-the event planner pays nothing). Planned vendors are {d['planned_percent_of_budget']}% of the budget "
                f"({_inr(d['planned_total'])} of {_inr(d['budget_total'])}).")
    if tool == "check_budget_reality":
        return f"{d['message']} ({d['basis']})"
    if tool == "search_knowledge":
        top = d.get("passages", [])[:2]
        return " ".join(f"{p['title']}: {p['text']}" for p in top) or NO_EVIDENCE
    return NO_EVIDENCE
