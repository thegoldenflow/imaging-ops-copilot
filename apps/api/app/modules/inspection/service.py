"""System 21 · Inspection Readiness Hub.

One place for what an inspector asks for: versioned policies, equipment records
(maintenance, physicist tests, repairs), staff credentials with expiry dates,
and quality records generated from peer review (13) and CT dose (14).
Credentials and equipment tests send reminders 60, 30 and 7 days before they
fall due and again when overdue; an inspection checklist shows what is in order.

Policy Q&A answers only from the uploaded documents. Keyword retrieval picks the
candidate sections; Claude answers with citations (section id plus an exact
quote). The server checks every citation against the retrieved text; when
nothing relevant is retrieved, or the model finds no answer, the reply says the
documents do not cover it."""

import math
import random
import re
from collections import Counter
from contextvars import ContextVar
from datetime import date, datetime, timedelta

from pydantic import BaseModel, model_validator

from app.core.models import MessageOutbox, Modality, Role
from app.core.store import Store
from app.llm.gateway import get_gateway
from app.llm.prompts import Prompt
from app.llm.providers import mock_fixture
from app.modules.inspection.policies import POLICIES

REMINDER_STAGES = [60, 30, 7, 0]  # days before the due date; 0 = overdue
POLICY_REVIEW_MONTHS = 24


class Section(BaseModel):
    id: str  # "<doc id>#s<n>"
    heading: str
    text: str


class Version(BaseModel):
    version: str
    effective: date
    change_note: str
    uploaded_by: str
    sections: list[Section]


class Document(BaseModel):  # FHIR DocumentReference
    id: str
    kind: str  # policy, equipment, credential, qc_record
    title: str
    category: str
    owner: str
    site_id: str | None = None
    scanner_id: str | None = None
    staff_id: str | None = None
    performed: date | None = None
    due: date | None = None  # expiry, next test or policy review date
    result: str | None = None
    source: str = "uploaded"
    versions: list[Version] = []

    @property
    def current(self) -> Version | None:
        return self.versions[-1] if self.versions else None


def documents(store: Store) -> dict[str, Document]:
    return store.module("inspection_documents", dict)


def reminders(store: Store) -> list[dict]:
    return store.module("inspection_reminders", list)


def qa_log(store: Store) -> list[dict]:
    return store.module("inspection_qa", list)


def due_status(due: date | None, today: date) -> str:
    if due is None:
        return "none"
    days = (due - today).days
    return "overdue" if days < 0 else "due_soon" if days <= REMINDER_STAGES[1] else "upcoming" if days <= REMINDER_STAGES[0] else "ok"


def split_sections(doc_id: str, text: str) -> list[Section]:
    """Markdown-ish: '# Heading' lines start sections; otherwise blank-line paragraphs."""
    sections, heading, buf = [], None, []

    def flush():
        body = " ".join(" ".join(buf).split())
        if body:
            sections.append(Section(id=f"{doc_id}#s{len(sections) + 1}", heading=heading or f"Part {len(sections) + 1}",
                                    text=body))

    for line in text.splitlines():
        if line.strip().startswith("#"):
            flush()
            heading, buf = line.strip().lstrip("#").strip(), []
        elif not line.strip():
            if buf and heading is None:
                flush()
                buf = []
            elif buf:
                buf.append("")
        else:
            buf.append(line.strip())
    flush()
    return sections


def add_policy(store: Store, *, doc_id: str | None, title: str, category: str, owner: str, text: str, version: str,
               effective: date, change_note: str, by: str) -> Document:
    doc = documents(store).get(doc_id) if doc_id else None
    if doc is None:
        doc = Document(id=doc_id or store.next_id("DOC"), kind="policy", title=title, category=category, owner=owner)
        documents(store)[doc.id] = doc
    sections = split_sections(doc.id, text)
    if not sections:
        raise ValueError("The document has no text")
    doc.versions.append(Version(version=version, effective=effective, change_note=change_note, uploaded_by=by,
                                sections=sections))
    doc.due = effective + timedelta(days=POLICY_REVIEW_MONTHS * 30)
    store.modules.pop("inspection_index", None)
    return doc


# ---------- Reminders and checklist ----------

def _contact(store: Store, doc: Document) -> str:
    if doc.staff_id and doc.staff_id in store.staff:
        return f"{store.staff[doc.staff_id].name.replace('Dr. ', '').lower().replace(' ', '.')}@example.com"
    return f"operations.{(doc.site_id or 'group').lower()}@example.com"


def process_reminders(store: Store, now: datetime, notify: bool = True) -> list[dict]:
    """Send each reminder stage once, when the item enters it."""
    today = now.date()
    sent = {(r["doc_id"], r["due"], r["stage"]) for r in reminders(store)}
    new = []
    for doc in list(documents(store).values()):
        if doc.kind not in ("credential", "equipment") or doc.due is None:
            continue
        days = (doc.due - today).days
        stage = 0 if days < 0 else next((s for s in sorted(REMINDER_STAGES)[1:] if days <= s), None)
        if stage is None or (doc.id, doc.due.isoformat(), stage) in sent:
            continue
        text = (f"{doc.title} is overdue since {doc.due:%b %d, %Y}" if stage == 0
                else f"{doc.title} is due on {doc.due:%b %d, %Y} ({days} days)")
        rem = {"id": store.next_id("REM"), "doc_id": doc.id, "due": doc.due.isoformat(), "stage": stage, "text": text,
               "to": _contact(store, doc), "sent_at": now.isoformat(timespec="minutes"), "acknowledged_by": None}
        reminders(store).append(rem)
        new.append(rem)
        if notify:
            msg = MessageOutbox(id=store.next_id("MSG"), channel="email", kind="inspection_reminder", patient_id=None,
                                to=rem["to"], language="en", body=f"Reminder: {text}. Please update the record in the "
                                "Inspection hub.", scheduled_for=now)
            store.outbox[msg.id] = msg
    if new:
        store.touch()
    return new


def checklist(store: Store, now: datetime) -> list[dict]:
    from app.modules.dose import service as dose
    from app.modules.peer_review import service as peer_review

    today = now.date()
    docs = list(documents(store).values())

    def item(key, label, failing: list[Document], evidence: list[Document], detail_ok: str, extra: str = ""):
        return {"key": key, "label": label, "ok": not failing, "detail": extra or (detail_ok if not failing else
                f"{len(failing)} not in order: " + "; ".join(d.title for d in failing[:4])),
                "evidence": [d.id for d in (failing or evidence)][:12]}

    def by(kind, category=None):
        return [d for d in docs if d.kind == kind and (category is None or d.category == category)]

    out = []
    for category, label in (("Annual physicist test", "Every scanner has a current physicist / performance test"),
                            ("Preventive maintenance", "Preventive maintenance is up to date on every scanner")):
        items = by("equipment", category)
        out.append(item(category, label, [d for d in items if d.due and d.due < today], items, f"{len(items)} scanners current"))
    for category, label in (("Registration", "Every clinical staff member's registration is current"),
                            ("BLS / CPR", "BLS / CPR is current for all clinical staff"),
                            ("MRI safety training", "MRI safety training is current for MRI staff")):
        items = by("credential", category)
        out.append(item(category, label, [d for d in items if d.due and d.due < today], items, f"{len(items)} on file"))
    policies = by("policy")
    out.append(item("policies", f"Every policy was reviewed in the last {POLICY_REVIEW_MONTHS} months",
                    [d for d in policies if d.due and d.due < today], policies, f"{len(policies)} policies current"))
    month = (today.replace(day=1) - timedelta(days=1)).strftime("%B %Y")
    qa = [d for d in by("qc_record") if d.category == "Peer review" and month in d.title]
    out.append({"key": "qa_report", "label": f"Peer review QA report for {month} is on file", "ok": bool(qa),
                "detail": "Generated from the peer review module" if qa else "Missing", "evidence": [d.id for d in qa]})
    stale = [r for r in dose.records(store).values() if dose.exceedance(store, r) and r.review is None
             and r.performed_at < now - timedelta(days=14)]
    dose_docs = [d for d in by("qc_record") if d.category == "CT dose"]
    out.append({"key": "dose_reviews", "label": "CT dose exceedances reviewed within 14 days", "ok": not stale,
                "detail": f"{len(stale)} exceedance(s) older than 14 days without review" if stale else "All reviewed in time",
                "evidence": [d.id for d in dose_docs][:3]})
    qa_overall = peer_review.qa_report(store)["overall"]
    out.append({"key": "qa_activity", "label": "Peer review is running (reviews completed in the period)",
                "ok": qa_overall.get("reviews", 0) > 0, "detail": f"{qa_overall.get('reviews', 0)} completed reviews",
                "evidence": [d.id for d in qa]})
    return out


# ---------- Grounded policy Q&A ----------

STOP = set("a an and are as at be by can do does for from has have how i if in is it its may must of on or our should "
           "that the their there this to was we what when where which who why will with without you your after before "
           "every any all than then they them these those into about over under within need needs".split())


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words if w not in STOP and len(w) > 1]


def _index(store: Store) -> dict:
    cached = store.modules.get("inspection_index")
    if cached:
        return cached
    chunks = []
    for doc in documents(store).values():
        if doc.kind == "policy" and doc.current:
            for sec in doc.current.sections:
                chunks.append({"id": sec.id, "doc_id": doc.id, "title": doc.title, "version": doc.current.version,
                               "heading": sec.heading, "text": sec.text,
                               "tf": Counter(_tokens(f"{doc.title} {sec.heading} {sec.heading} {sec.text}"))})
    df = Counter(t for c in chunks for t in c["tf"])
    idx = {"chunks": chunks, "idf": {t: math.log(1 + len(chunks) / n) for t, n in df.items()}}
    store.modules["inspection_index"] = idx
    return idx


MIN_SCORE = 2.5


def retrieve(store: Store, question: str, k: int = 4) -> list[dict]:
    idx = _index(store)
    q = set(_tokens(question))
    scored = []
    for c in idx["chunks"]:
        score = sum(idx["idf"].get(t, 0) * (1 + math.log(c["tf"][t])) for t in q if c["tf"].get(t))
        if score >= MIN_SCORE:
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [{**c, "score": round(s, 2)} for s, c in scored[:k]]


_RETRIEVED: ContextVar[dict[str, str]] = ContextVar("inspection_retrieved", default={})


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


class Citation(BaseModel):
    chunk_id: str
    quote: str


class QaOutput(BaseModel):
    found: bool
    answer: str
    citations: list[Citation]

    @model_validator(mode="after")
    def grounded(self):
        chunks = _RETRIEVED.get()
        for c in self.citations:
            if c.chunk_id not in chunks:
                raise ValueError(f"Citation {c.chunk_id} is not one of the excerpts")
            if not c.quote.strip() or _norm(c.quote) not in _norm(chunks[c.chunk_id]):
                raise ValueError(f"The quote for {c.chunk_id} is not verbatim text from that excerpt")
        if self.found and not self.citations:
            raise ValueError("An answer needs at least one citation")
        return self


QA_PROMPT = Prompt(
    name="policy_qa",
    version="policy_qa@1",
    system=(
        "You answer staff questions about clinic policies using only the policy excerpts provided. Each excerpt has an id "
        "in square brackets. Cite every statement with the excerpt id and an exact quote copied from that excerpt. "
        "If the excerpts do not answer the question, set found to false, say that the documents do not cover it, and "
        "give no citations. Do not use outside knowledge."
    ),
    template="Question: {question}\n\nExcerpts:\n{excerpts}",
)


@mock_fixture("policy_qa")
def _mock(text: str, images: list, attempt: int) -> dict:
    """Extractive baseline: the excerpt sentence sharing the most words with the question."""
    question = text.split("Question: ", 1)[1].split("\n", 1)[0]
    q = set(_tokens(question))
    best = (0, None, "")
    for m in re.finditer(r"^\[([^\]]+)\] [^\n]*\n(.+)$", text.split("Excerpts:\n", 1)[1], re.M):
        for sentence in re.split(r"(?<=[.!?])\s+", m.group(2).strip()):
            overlap = len(q & set(_tokens(sentence)))
            if overlap > best[0]:
                best = (overlap, m.group(1), sentence)
    if best[0] < 2 or best[0] < 0.5 * len(q):  # most of the question must be covered by one sentence
        return {"found": False, "answer": "The uploaded documents do not cover this question.", "citations": []}
    return {"found": True, "answer": best[2], "citations": [{"chunk_id": best[1], "quote": best[2]}]}


NOT_FOUND = "I could not find this in the uploaded documents. Check with the document owner or upload the relevant policy."


def ask(store: Store, question: str, by: str, now: datetime) -> dict:
    chunks = retrieve(store, question)
    entry = {"id": store.next_id("QA"), "question": question.strip(), "asked_by": by, "asked_at": now.isoformat(timespec="minutes"),
             "retrieved": [{"chunk_id": c["id"], "score": c["score"]} for c in chunks]}
    if not chunks:
        entry |= {"found": False, "answer": NOT_FOUND, "citations": [], "ai_status": "no_match", "model": None,
                  "prompt_version": None}
    else:
        excerpts = "\n\n".join(f"[{c['id']}] {c['title']} (v{c['version']}) · {c['heading']}\n{c['text']}" for c in chunks)
        token = _RETRIEVED.set({c["id"]: c["text"] for c in chunks})
        try:
            outcome = get_gateway().structured(task="policy_qa", prompt=QA_PROMPT,
                                               variables={"question": question.strip(), "excerpts": excerpts},
                                               schema_cls=QaOutput, tier="reasoning")
        finally:
            _RETRIEVED.reset(token)
        by_id = {c["id"]: c for c in chunks}
        if outcome.status == "ok":
            data = outcome.data
            entry |= {"found": data["found"], "answer": data["answer"] if data["found"] else NOT_FOUND,
                      "citations": [{"chunk_id": c["chunk_id"], "quote": c["quote"], "doc_id": by_id[c["chunk_id"]]["doc_id"],
                                     "title": by_id[c["chunk_id"]]["title"], "heading": by_id[c["chunk_id"]]["heading"],
                                     "version": by_id[c["chunk_id"]]["version"]} for c in data["citations"]]}
        else:
            entry |= {"found": False, "citations": [], "answer": "AI is unavailable or its answer could not be verified "
                      "against the documents. The closest sections are listed below.",
                      "suggested": [{"chunk_id": c["id"], "doc_id": c["doc_id"], "title": c["title"], "heading": c["heading"]}
                                    for c in chunks]}
        entry |= {"ai_status": outcome.status, "model": outcome.model, "prompt_version": outcome.prompt_version,
                  "llm_call_id": outcome.call_id}
    qa_log(store).append(entry)
    store.touch()
    return entry


# ---------- Seed ----------

def _scanners_tests(s: Store, rng: random.Random, today: date) -> None:
    for sc in s.scanners.values():
        site = sc.site_id
        tests = [("Preventive maintenance", 182, "Service engineer (vendor)", "Completed; no faults found"),
                 ("Annual physicist test", 365, "Medical physicist", "Pass")]
        for category, interval, owner, result in tests:
            performed = today - timedelta(days=rng.randint(30, interval - 40))
            doc = Document(id=f"EQ-{sc.id}-{'PM' if category.startswith('Prev') else 'PHYS'}", kind="equipment",
                           title=f"{sc.id} {category.lower()}", category=category, owner=owner, site_id=site,
                           scanner_id=sc.id, performed=performed, due=performed + timedelta(days=interval), result=result,
                           source="equipment record")
            documents(s)[doc.id] = doc
        doc = Document(id=f"EQ-{sc.id}-DQC", kind="equipment", title=f"{sc.id} daily QC log", category="Daily QC",
                       owner="Technologists", site_id=site, scanner_id=sc.id, performed=today - timedelta(days=1),
                       result="All daily checks passed in the last 30 days", source="equipment record")
        documents(s)[doc.id] = doc
    # Planted: a physicist test coming due, an overdue maintenance and a repair on file.
    documents(s)["EQ-EVW-CT1-PHYS"].due = today + timedelta(days=20)
    pm = documents(s)["EQ-NGT-MRI1-PM"]
    pm.due, pm.performed = today - timedelta(days=3), today - timedelta(days=185)
    documents(s)["EQ-LKS-CT1-REP1"] = Document(
        id="EQ-LKS-CT1-REP1", kind="equipment", title="LKS-CT1 repair: tube cooling fan replaced", category="Repair",
        owner="Service engineer (vendor)", site_id="LKS", scanner_id="LKS-CT1", performed=today - timedelta(days=41),
        result="Returned to service after QC pass", source="equipment record")


def _credentials(s: Store, rng: random.Random, today: date) -> None:
    for u in s.staff.values():
        if u.role in (Role.ADMIN, Role.OPERATIONS_MANAGER, Role.REFERRER):
            continue
        creds = [("BLS / CPR", 730)]
        if u.role in (Role.RADIOLOGIST, Role.MEDICAL_DIRECTOR, Role.TECHNOLOGIST):
            creds.append(("Registration", 365))
        if u.role == Role.TECHNOLOGIST or Modality.MRI in u.reading_modalities:
            creds.append(("MRI safety training", 365))
        for category, length in creds:
            issued = today - timedelta(days=rng.randint(20, length - 70))
            doc = Document(id=f"CRED-{u.id}-{category.split()[0].upper()}", kind="credential",
                           title=f"{u.name}: {category}",
                           category=category, owner=u.name, staff_id=u.id, site_id=u.site_ids[0] if u.site_ids else None,
                           performed=issued, due=issued + timedelta(days=length), source="credential file")
            documents(s)[doc.id] = doc
    documents(s)["CRED-U-TECH2-REGISTRATION"].due = today + timedelta(days=12)
    documents(s)["CRED-U-RAD2-BLS"].due = today - timedelta(days=5)
    documents(s)["CRED-U-TECH-MRI"].due = today + timedelta(days=45)


def _qc_records(s: Store, rng: random.Random, now: datetime) -> None:
    from app.modules.dose import service as dose
    from app.modules.peer_review import service as peer_review

    report = peer_review.qa_report(s)["overall"]
    today = now.date()
    for back in (1, 2, 3):
        month_end = today.replace(day=1) - timedelta(days=1)
        for _ in range(back - 1):
            month_end = month_end.replace(day=1) - timedelta(days=1)
        doc = Document(id=f"QC-PR-{month_end:%Y%m}", kind="qc_record", title=f"Peer review QA summary, {month_end:%B %Y}",
                       category="Peer review", owner="QA lead", performed=month_end, source="generated from peer review (system 13)",
                       result=(f"{report.get('reviews', 0)} completed reviews to date; concurrence "
                               f"{round(100 * (report.get('concur_rate') or 0))}%; significant discrepancies sent to the QA committee."))
        documents(s)[doc.id] = doc
    recs = list(dose.records(s).values())
    over = [r for r in recs if dose.exceedance(s, r)]
    doc = Document(id=f"QC-DOSE-{today:%Y%m}", kind="qc_record", title=f"CT dose review, {today:%B %Y} (to date)",
                   category="CT dose", owner="Radiation safety officer", performed=today, source="generated from CT dose (system 14)",
                   result=(f"{len(recs)} CT exams with dose records in 90 days; {len(over)} above reference level, "
                           f"{sum(r.review is not None for r in over)} reviewed. EVW-CT1 trending upward; physicist review requested."))
    documents(s)[doc.id] = doc


def seed(s: Store, rng: random.Random, now: datetime) -> None:
    today = now.date()
    for doc_id, title, owner, version, days_ago, note, sections in POLICIES:
        text = "\n\n".join(f"# {h}\n{t}" for h, t in sections)
        major, minor = version.split(".")
        if note:  # keep the previous version for the history view
            previous = "\n\n".join(f"# {h}\n{t}" for h, t in sections if h != "Access monitoring")
            previous = previous.replace("within the last 90 days", "within the last 180 days").replace(
                "within 24 hours", "as soon as possible")
            add_policy(s, doc_id=doc_id, title=title, category="Policy", owner=owner, text=previous,
                       version=f"{int(major) - 1 if minor == '0' else major}.{9 if minor == '0' else int(minor) - 1}",
                       effective=today - timedelta(days=days_ago + 400), change_note="Previous version", by=owner)
        add_policy(s, doc_id=doc_id, title=title, category="Policy", owner=owner, text=text, version=version,
                   effective=today - timedelta(days=days_ago), change_note=note or "Reviewed, no changes", by=owner)
    # One policy is past its review date.
    documents(s)["POL-ID"].due = today - timedelta(days=30)
    _scanners_tests(s, rng, today)
    _credentials(s, rng, today)
    _qc_records(s, rng, now)
    # Reminders that would already have gone out; new stages are sent by the worker.
    process_reminders(s, now - timedelta(minutes=5), notify=False)
