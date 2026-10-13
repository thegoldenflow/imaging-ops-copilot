"""Traceability validators (spec 7.3 "溯源与幻觉防护"): one base class, one subclass per document type.

The base class walks the document's fact items and checks, for each:

1. sources: the item cites at least one source, every cited reference is one of the
   document's inputs (the id lists the model was given), and at least one is of a
   type the field allows (a medication line citing a Condition does not count);
2. typed names (the fields a subclass declares traceable): the item's name or code
   must match an input resource of the right kind, by code first, then by name
   (lexicon.same_name: normalised equality, containment, difflib >= 0.85, with
   synonyms) — and the match must be among the item's own sources;
3. free text: every drug name in the item's text (the drug lexicon: the EHR's
   medications and the knowledge graph's drugs) must be a medication of the input;
   condition and procedure names (the EHR's concepts) must be among its diagnoses
   and procedures.

A failure is `red` (untraceable: blocks signing); `amber` issues are warnings shown
to the signer. Subclasses declare `RULES` (field path pattern -> what is checked)
and may add checks. The validator is deterministic code over the draft and its
inputs; it runs on the model's raw output (the raw hallucination rate, a monitoring
metric) and again on every edit and before signing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

from app.modules.documentation import lexicon as L
from app.modules.documentation.facts import Source
from app.modules.documentation.models import Issue, ValidationReport

MED_TYPES = ("MedicationRequest", "MedicationStatement", "DocumentReference")
NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?!\w|\.\d)")  # a number may end a sentence


@dataclass(frozen=True)
class Rule:
    """What a field's items must satisfy."""

    kind: str | None = None  # the traceable kind: diagnosis, procedure, medication, test, or None (sources only)
    name: str = "name"  # the item's attribute holding the name
    code: str | None = None  # the item's attribute holding the code
    ref_types: tuple[str, ...] = ()  # cited sources must include one of these types ("" = any input)
    match_kinds: tuple[str, ...] = ()  # source kinds a name may match
    text_fields: tuple[str, ...] = ("text",)
    needs_refs: bool = True


@dataclass
class Item:
    path: str
    data: dict
    rule: Rule


@dataclass
class Index:
    sources: dict[str, Source]
    by_kind: dict[str, list[Source]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for s in self.sources.values():
            self.by_kind.setdefault(s.kind, []).append(s)

    def kinds(self, *kinds: str) -> list[Source]:
        return [s for k in kinds for s in self.by_kind.get(k, [])]


def matches(source: Source, name: str, code: str | None) -> bool:
    if code and source.code and code == source.code:
        return True
    return bool(name) and any(L.same_name(name, n) for n in [source.name, *source.names] if n)


class TraceabilityValidator:
    """Base: walk the items a subclass declares, check sources, names and free text."""

    NAME = "traceability"
    RULES: dict[str, Rule] = {}  # path pattern ("medications.*[]", "hospital_course[]", "situation") -> Rule
    MEDICATION_KINDS = ("medication",)
    CONDITION_KINDS = ("diagnosis", "encounter")
    PROCEDURE_KINDS = ("procedure",)

    def __init__(self, sources: dict[str, Source]) -> None:
        self.index = Index(sources)

    # ----- walking -----

    def items(self, content: dict) -> Iterator[Item]:
        for pattern, rule in self.RULES.items():
            yield from self._expand(content, pattern.split("."), "", rule)

    def _expand(self, node, parts: list[str], prefix: str, rule: Rule) -> Iterator[Item]:
        if not parts:
            if isinstance(node, dict):
                yield Item(prefix, node, rule)
            return
        head, rest = parts[0], parts[1:]
        listed = head.endswith("[]")
        key = head[:-2] if listed else head
        if not isinstance(node, dict):
            return
        children = list(node.items()) if key == "*" else [(key, node.get(key))]
        for name, child in children:
            path = f"{prefix}.{name}" if prefix else name
            if listed:
                for i, element in enumerate(child or []):
                    yield from self._expand(element, rest, f"{path}[{i}]", rule)
            elif child is not None:
                yield from self._expand(child, rest, path, rule)

    # ----- checks -----

    def validate(self, content: dict) -> ValidationReport:
        report = ValidationReport(validator=self.NAME)
        for item in self.items(content):
            report.checked += 1
            before = len(report.red())
            report.issues += self.check_item(item)
            if len(report.red()) == before:
                report.traceable += 1
        report.issues += self.check_document(content)
        return report

    def check_document(self, content: dict) -> list[Issue]:
        return []

    def _cited(self, item: Item) -> tuple[list[Source], list[Issue]]:
        refs = [r for r in item.data.get("source_refs") or [] if isinstance(r, str)]
        issues, cited = [], []
        if not refs and item.rule.needs_refs:
            issues.append(Issue(path=item.path, severity="red", code="no_source",
                                message="no source cited: every fact needs the resource it comes from"))
        for r in refs:
            s = self.index.sources.get(r)
            if s is None:
                issues.append(Issue(path=item.path, severity="red", code="unknown_ref", value=r,
                                    message=f"cites {r}, which is not one of this document's inputs"))
            else:
                cited.append(s)
        if cited and item.rule.ref_types and not any(s.rtype in item.rule.ref_types for s in cited):
            issues.append(Issue(path=item.path, severity="red", code="wrong_source_type",
                                value=", ".join(s.ref for s in cited),
                                message=f"the cited sources are not a {' or '.join(item.rule.ref_types)}"))
        return cited, issues

    def check_item(self, item: Item) -> list[Issue]:
        cited, issues = self._cited(item)
        rule = item.rule
        if rule.kind:
            name = str(item.data.get(rule.name) or "")
            code = str(item.data.get(rule.code) or "") if rule.code else None
            pool = self.index.kinds(*rule.match_kinds)
            found = [s for s in pool if matches(s, name, code)]
            if not found:
                issues.append(Issue(path=item.path, severity="red", code=f"untraceable_{rule.kind}", value=name,
                                    message=f"{rule.kind} '{name}' does not match any {rule.kind} in the input"))
            elif cited and not any(s in found for s in cited):
                issues.append(Issue(path=item.path, severity="red", code="source_mismatch", value=name,
                                    message=f"'{name}' is in the input ({found[0].ref}) but not in the sources this "
                                            f"item cites"))
        flagged = {i.value.lower() for i in issues if i.code.startswith("untraceable_")}
        for attr in rule.text_fields:  # a name already flagged by its typed check is not flagged again
            issues += [i for i in self.scan_text(item.path, str(item.data.get(attr) or ""))
                       if i.value.lower() not in flagged]
        return issues

    def scan_text(self, path: str, text: str) -> list[Issue]:
        """Drug, condition and procedure names in free text must be in the input."""
        if not text:
            return []
        issues = []
        meds = self.index.kinds(*self.MEDICATION_KINDS)
        for phrase, key in L.find_mentions(text, L.drug_lexicon()):
            code = key.split(":", 1)[1] if key.startswith("rxcui:") else None
            if not any(matches(s, phrase, code) for s in meds):
                issues.append(Issue(path=path, severity="red", code="untraceable_drug", value=phrase,
                                    message=f"drug '{phrase}' in the text is not a medication of the input"))
        conditions = self.index.kinds(*self.CONDITION_KINDS)
        for phrase, code in L.find_mentions(text, L.condition_lexicon()):
            if not any(matches(s, phrase, code) for s in conditions):
                issues.append(Issue(path=path, severity="red", code="untraceable_diagnosis", value=phrase,
                                    message=f"condition '{phrase}' in the text is not a diagnosis of the input"))
        procedures = self.index.kinds(*self.PROCEDURE_KINDS)
        for phrase, code in L.find_mentions(text, L.procedure_lexicon()):
            if not any(matches(s, phrase, code) for s in procedures):
                issues.append(Issue(path=path, severity="red", code="untraceable_procedure", value=phrase,
                                    message=f"procedure '{phrase}' in the text is not a procedure of the input"))
        return issues


class DischargeSummaryValidator(TraceabilityValidator):
    """Drugs, diagnoses and procedures must trace to the encounter's resources (7.3)."""

    NAME = "DischargeSummaryValidator"
    RULES = {
        "reason_for_admission": Rule(),
        "hospital_course[]": Rule(),
        "discharge_diagnoses[]": Rule(kind="diagnosis", code="code", ref_types=("Condition", "Encounter"),
                                      match_kinds=("diagnosis", "encounter")),
        "procedures[]": Rule(kind="procedure", code="code", ref_types=("Procedure",), match_kinds=("procedure",),
                             text_fields=("text",)),
        "medications.*[]": Rule(kind="medication", code="rxcui", ref_types=MED_TYPES, match_kinds=("medication",),
                                text_fields=("text", "note")),
        "pending_results[]": Rule(kind="test", name="test", ref_types=("ServiceRequest", "Observation",
                                                                      "DiagnosticReport"),
                                  match_kinds=("pending", "result", "report")),
        "follow_up[]": Rule(text_fields=("text", "department", "location")),
        "red_flags[]": Rule(),
    }

    def check_item(self, item: Item) -> list[Issue]:
        issues = super().check_item(item)
        if item.path.startswith("medications.") and self.table_categories:
            category = item.path.split(".")[1].split("[")[0]
            name, code = str(item.data.get("name") or ""), str(item.data.get("rxcui") or "")
            listed = {cat for cat, n, c in self.table_categories if (code and code == c) or L.same_name(name, n)}
            if listed and category not in listed:
                issues.append(Issue(path=item.path, severity="amber", code="category_differs", value=name,
                                    message=f"the discharge medication table lists {name} as "
                                            f"{' / '.join(sorted(listed))}, not {category}"))
        return issues

    def __init__(self, sources: dict[str, Source], table: dict | None = None) -> None:
        super().__init__(sources)
        self.table_categories = [(cat, line.get("name") or "", line.get("rxcui") or "")
                                 for cat in ("new", "changed", "stopped", "continued")
                                 for line in (table or {}).get(cat) or []]

    def check_document(self, content: dict) -> list[Issue]:
        issues = []
        meds = content.get("medications") or {}
        if self.table_categories:
            listed = [line for cat in ("new", "changed", "stopped", "continued") for line in meds.get(cat) or []]
            for cat, name, code in self.table_categories:
                if not any((code and code == str(m.get("rxcui") or "")) or L.same_name(name, m.get("name") or "")
                           for m in listed):
                    issues.append(Issue(path=f"medications.{cat}", severity="amber", code="table_line_missing",
                                        value=name, message=f"{name} ({cat}) from the discharge medication table is "
                                                            f"not in the summary"))
        if not content.get("discharge_diagnoses"):
            issues.append(Issue(path="discharge_diagnoses", severity="amber", code="empty",
                                message="no discharge diagnosis listed"))
        return issues


class PatientInstructionsValidator(TraceabilityValidator):
    """Every medicine, follow-up and reason must be an item of the signed summary, with the same name, category,
    dose and date; codes must be in the menus (7.3: the instructions use only the signed summary)."""

    NAME = "PatientInstructionsValidator"
    RULES = {
        "reason[]": Rule(kind="reason", ref_types=(), match_kinds=("diagnosis", "procedure"), text_fields=()),
        "medicines[]": Rule(kind="medication", code="rxcui", match_kinds=("medication",), text_fields=()),
        "follow_ups[]": Rule(kind="follow-up", name="department", match_kinds=("followup",), text_fields=()),
    }

    def __init__(self, summary_items: dict[str, dict], menus: dict[str, set[str]]) -> None:
        sources = {}
        for path, item in summary_items.items():
            kind = ("medication" if path.startswith("medications.") else "diagnosis"
                    if path.startswith("discharge_diagnoses") else "procedure" if path.startswith("procedures")
                    else "followup" if path.startswith("follow_up") else "other")
            name = item.get("name") or item.get("test") or item.get("department") or item.get("text") or ""
            sources[path] = Source(path, "SummaryItem", kind, name=name, code=str(item.get("rxcui") or
                                   item.get("code") or ""), names=[name])
        super().__init__(sources)
        self.summary_items = summary_items
        self.menus = menus

    def check_item(self, item: Item) -> list[Issue]:
        issues = []
        path, data = item.path, item.data
        target = self.summary_items.get(str(data.get("item") or ""))
        if target is None:
            return [Issue(path=path, severity="red", code="unknown_item", value=str(data.get("item")),
                          message=f"{data.get('item')!r} is not an item of the signed discharge summary")]
        if path.startswith("medicines"):
            summary_cat = str(data.get("item")).split(".")[1].split("[")[0]
            if not (L.same_name(data.get("name") or "", target.get("name") or "")):
                issues.append(Issue(path=path, severity="red", code="untraceable_medication", value=data.get("name"),
                                    message=f"'{data.get('name')}' is not the summary's {target.get('name')}"))
            if data.get("category") != summary_cat:
                issues.append(Issue(path=path, severity="red", code="category_differs", value=data.get("category"),
                                    message=f"the summary lists it as {summary_cat}"))
            if data.get("purpose") not in self.menus["purposes"]:
                issues.append(Issue(path=path, severity="red", code="unknown_code", value=data.get("purpose"),
                                    message="purpose is not in the menu"))
            for code in (data.get("times") or []):
                if code not in self.menus["times"]:
                    issues.append(Issue(path=path, severity="red", code="unknown_code", value=code,
                                        message=f"time of day {code!r} is not in the menu"))
            for code in (data.get("cautions") or []):
                if code not in self.menus["cautions"]:
                    issues.append(Issue(path=path, severity="red", code="unknown_code", value=code,
                                        message=f"caution {code!r} is not in the menu"))
            if summary_cat != "stopped" and not data.get("times"):
                issues.append(Issue(path=path, severity="amber", code="no_times", value=data.get("name"),
                                    message="no time of day for a medicine the patient takes"))
        elif path.startswith("follow_ups"):
            if (data.get("when") or "") != (target.get("when") or ""):
                issues.append(Issue(path=path, severity="red", code="date_differs", value=data.get("when"),
                                    message=f"the summary's date is {target.get('when') or 'not set'}"))
            if data.get("department") not in self.menus["departments"]:
                issues.append(Issue(path=path, severity="red", code="unknown_code", value=data.get("department"),
                                    message="department is not in the menu"))
            elif data.get("department") == "other":
                issues.append(Issue(path=path, severity="amber", code="untranslated", value=target.get("department"),
                                    message="department not in the phrase table: shown as recorded in every language"))
        elif path.startswith("reason"):
            if not str(data.get("item")).startswith(("discharge_diagnoses", "procedures")):
                issues.append(Issue(path=path, severity="red", code="wrong_item", value=data.get("item"),
                                    message="the reason must be a diagnosis or a procedure of the summary"))
        return issues

    def check_document(self, content: dict) -> list[Issue]:
        issues = []
        for name, menu in (("red_flags", "red_flags"), ("daily_care", "daily_care")):
            for i, flag in enumerate(content.get(name) or []):
                if flag.get("code") not in self.menus[menu]:
                    issues.append(Issue(path=f"{name}[{i}]", severity="red", code="unknown_code",
                                        value=str(flag.get("code")), message=f"{flag.get('code')!r} is not in the menu"))
                for because in flag.get("because") or []:
                    if because not in self.summary_items:
                        issues.append(Issue(path=f"{name}[{i}]", severity="red", code="unknown_item", value=because,
                                            message=f"{because!r} is not an item of the signed summary"))
        listed = {m.get("item") for m in content.get("medicines") or []}
        for path, item in self.summary_items.items():
            if path.startswith("medications.") and path not in listed:
                issues.append(Issue(path="medicines", severity="amber", code="summary_medicine_missing",
                                    value=item.get("name") or path,
                                    message=f"{item.get('name')} from the summary is not in the medicine table"))
        return issues

    def scan_rendered(self, text: str) -> list[Issue]:
        """Drug names in the rendered English text must be medicines of the summary."""
        issues = []
        meds = [s for s in self.index.sources.values() if s.kind == "medication"]
        for phrase, key in L.find_mentions(text, L.drug_lexicon()):
            code = key.split(":", 1)[1] if key.startswith("rxcui:") else None
            if not any(matches(s, phrase, code) for s in meds):
                issues.append(Issue(path="rendered", severity="red", code="untraceable_drug", value=phrase,
                                    message=f"drug '{phrase}' in the instructions is not a medicine of the summary"))
        return issues


INTAKE_OUTPUT = re.compile(r"\b(?:intake|output|urine|fluid balance)\b", re.I)


def _gap_evidence(topic: str, sources: list[Source]) -> list[Source]:
    """The inputs that show a `not_recorded` topic IS recorded (empty: the declaration holds)."""
    if topic == "intake_output":  # no input kind carries it today; a result named like it would
        return [s for s in sources if s.kind == "result" and INTAKE_OUTPUT.search(s.name)]
    if topic == "vital_signs":
        return [s for s in sources if s.kind == "observation"]
    if topic == "pain":
        return [s for s in sources if s.kind == "observation" and "pain" in s.detail.lower()]
    if topic == "laboratory_results":
        return [s for s in sources if s.kind == "result" and not INTAKE_OUTPUT.search(s.name)]
    if topic == "open_tasks":
        return [s for s in sources if s.kind == "task"]
    if topic == "flags":
        return [s for s in sources if s.kind == "flag"]
    return []


class HandoffValidator(TraceabilityValidator):
    """SBAR: every statement cites the 12-hour inputs; drugs must be the encounter's medications; numbers should be
    in the cited sources (a warning). A topic declared `not_recorded` must have no input (an absence has nothing to
    cite, so it is checked against the whole input instead): declaring a recorded topic missing is red."""

    NAME = "HandoffValidator"
    RULES = {"situation": Rule(), "background[]": Rule(), "assessment[]": Rule(), "recommendation[]": Rule()}

    def check_document(self, content: dict) -> list[Issue]:
        issues = []
        for i, topic in enumerate(content.get("not_recorded") or []):
            evidence = _gap_evidence(str(topic), list(self.index.sources.values()))
            if evidence:
                issues.append(Issue(path=f"not_recorded[{i}]", severity="red", code="recorded_not_missing",
                                    value=str(topic), message=f"declared not recorded, but the input has it: "
                                                              f"{', '.join(s.ref for s in evidence[:3])}"))
        return issues

    def check_item(self, item: Item) -> list[Issue]:
        issues = super().check_item(item)
        cited = [self.index.sources[r] for r in item.data.get("source_refs") or [] if r in self.index.sources]
        if cited:
            known = " ".join(f"{s.detail} {s.name} {s.date}" for s in cited)
            for number in NUMBER.findall(str(item.data.get("text") or "")):
                if number not in NUMBER.findall(known) and len(number) > 1:
                    issues.append(Issue(path=item.path, severity="amber", code="number_not_in_source", value=number,
                                        message=f"{number} does not appear in the cited sources"))
        return issues
