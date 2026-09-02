"""Steps 1–4: keywords, normalise, scope gate, resolve county and declared state.

Rules run always. Claude Haiku (when configured) refines intent and declared
state with a closed-enum structured output; the rules' out-of-scope hits stay
as a hard gate either way (uncertain -> out of scope, SPEC §9.2 step 2).

Scope logic: a message is split into clauses. A clause carrying a hard
out-of-scope signal is refused even if it also contains an in-scope word
("mbolea kwa mkopo" is a loan question, not a fertiliser question). Only clean
clauses contribute intents; refused clauses become explicit declines when at
least one clean clause exists (compound asks, SPEC §4). Prompt injection and
personal-record lookups refuse the whole message.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from nitapata.constants import DECLARED_FLAGS, INTENTS

log = logging.getLogger("nitapata.intents")

KEYWORDS = {"HELP": "help", "MSAADA": "help", "STOP": "stop", "EN": "lang_en", "SW": "lang_sw"}

IN_SCOPE = {
    "price": r"\b(bei|pesa ngapi|ngapi|gharama|price|cost|how much|shilingi|ksh|dap|urea|npk|mop)\b",
    "eligibility": r"\b(sifa|nastahili|kustahili|qualif\w*|eligib\w*|naweza pata|can i get|entitled|nahitaji|ninahitaji|what do i need|who qualifies|nimekosa)\b",
    "registration_process": r"\b(usajili|kujisajili|jisajili|jiandikish\w*|regist\w*|kiamis|nimekosa nini|steps?)\b",
    "depot_availability": r"\b(depo ya|depot \w+ inanihudumia|inanihudumia|inahudumia|wapi|where|collect\w*|nachukua|chukua|kuchukua|vituo|kituo|stock|imefika|mbolea ya \w+ bado|serves?)\b",
    "evoucher_redemption": r"\b(voucher|vocha|e-?voucher|redeem|nikuje na nini|nikienda depot|nibebe nini|what do i bring|what should i bring|mgao|allocation|malipo|nalipa|lipa|pay)\b",
    "cycle_timing": r"\b(msimu|inaanza lini|lini|when does|when is|season|cycle|mzunguko|tarehe|dates?)\b",
}
OUT_OF_SCOPE = {  # hard: refuse the clause
    "credit": r"\b(mkopo|mikopo|loan|credit|deni|kukopa|lipa baadaye|kopesha)\b",
    "insurance": r"\b(bima|insurance|cover|premium)\b",
    "agronomy": r"\b(panda|kupanda|nipande|nitapanda|atapanda|alipanda|tutapanda|plant\w*|lima|kulima|mbegu gani|which seed|spray|dawa|nyunyiz\w*|mavuno|yield|harvest|nitavuna|inakauka|kunyauka|wadudu|pests?|ugonjwa|disease|top ?dress\w*|niweke|weke|nitumie|tumie|apply)\b",
    "weather": r"\b(mvua|rain|weather|hali ya hewa|forecast|itanyesha)\b",
}
GLOBAL_OUT_OF_SCOPE = {  # refuse the whole message
    "personal_record": r"\b(am i registered|niko kwenye rejista|nipo kwenye list|is my name|jina langu liko|status yangu|check (my|if i)|angalia kama|kama nimesajiliwa|hali ya usajili wangu)\b",
    "injection": r"(ignore (all |your |the |previous )*(rules|instructions)|sahau sheria|system prompt|pretend you are|act as|jibu kama wewe ni)",
}
SOFT = {  # decline modifiers, never scope killers
    "comparison": r"\b(imepanda|imeshuka|gone up|gone down|compared|kuliko|last year|mwaka jana|ilikuwa|msimu uliopita|previous cycle|changed|imebadilika|bado ni)\b",
}
COUNTIES = {
    "kakamega": r"\b(kakamega|malava|mumias|butere|shinyalu|lurambi|matungu|likuyani|navakholo|khwisero|ikolomani|lugari)\b",
    "machakos": r"\b(machakos|kangundo|matungulu|mitaboni|masii|kathiani|mwala|yatta|athi river|mavoko)\b",
}
PLACES = r"\b(kangundo|malava|mumias|butere|matungulu|masii|shinyalu|mitaboni|lugari)\b"
CLAUSE_SPLIT = r"[?;.!]|\bna pia\b|\bna\b|\band\b|\balso\b|\bpia\b|\blakini\b|\bbut\b"

# Declared state (SPEC §4.1). Only explicit statements map; everything else is discarded.
DECLARED = [
    ("has_id", True, r"\b(nina (id|kitambulisho)|niko na (id|kitambulisho)|nimepata (id|kitambulisho)|i have (my |an? )?(id|national id))\b"),
    ("has_id", False, r"\b(sina (id|kitambulisho)|hakuna kitambulisho|no id|without (my )?id|i (don'?t|do not) have (my |an? )?id)\b"),
    ("is_registered_kiamis", True, r"\b(nimesajiliwa|nimejisajili|i am registered|i'?m registered|already registered)\b"),
    ("is_registered_kiamis", False, r"\b(sijasajiliwa|sijajisajili|si kwenye rejista|hakuna usajili|not registered|haven'?t registered|never registered)\b"),
    ("has_allocation_sms", True, r"\b(nimepata sms ya mgao|nina sms ya mgao|got the allocation sms|have the allocation sms)\b"),
    ("has_allocation_sms", False, r"\b(sina sms ya mgao|sijapata sms( ya mgao)?|no allocation sms|haven'?t (got|received) (the |an? )?(allocation )?sms)\b"),
]
EN_MARKERS = r"\b(the|price|how much|where|what|need|register|cost|when|can i|is|are|do i|which|my|should|for|who)\b"
SW_MARKERS = r"\b(bei|ngapi|mbolea|nahitaji|nini|wapi|depo|kaunti|msimu|nipande|sina|nina|sawa|ya|na|kwa|ni|je|inanihudumia|nikuje|nikienda|lini|bado|sijasajiliwa|nimesajiliwa|ruzuku|mkopo|bima|mvua|nifanye|habari)\b"


@dataclass
class Analysis:
    text: str
    keyword: str | None = None
    language: str = "sw"
    intents: list[str] = field(default_factory=list)      # ordered by position in the message
    out_of_scope: list[str] = field(default_factory=list)  # reasons (hard + soft)
    hard_out_of_scope: bool = False                        # whole message refused
    county: str | None = None
    place: str | None = None
    declared: dict = field(default_factory=dict)
    classifier: str = "rules"


def _hits(patterns: dict[str, str], text: str) -> dict[str, int]:
    out = {}
    for k, p in patterns.items():
        m = re.search(p, text, re.IGNORECASE)
        if m:
            out[k] = m.start()
    return out


def detect_language(text: str) -> str:
    en = len(re.findall(EN_MARKERS, text, re.IGNORECASE))
    sw = len(re.findall(SW_MARKERS, text, re.IGNORECASE))
    return "en" if en > sw else "sw"


def analyse(text: str) -> Analysis:
    raw = text.strip()
    a = Analysis(text=re.sub(r"\s+", " ", raw))
    kw = KEYWORDS.get(raw.upper())
    if kw:
        a.keyword = kw
        return a
    a.language = detect_language(a.text)

    for county, pat in COUNTIES.items():
        if re.search(pat, a.text, re.IGNORECASE):
            a.county = county
            break
    m = re.search(PLACES, a.text, re.IGNORECASE)
    a.place = m.group(0).title() if m else None

    global_hits = _hits(GLOBAL_OUT_OF_SCOPE, a.text)
    soft_hits = _hits(SOFT, a.text)
    hard_any = _hits(OUT_OF_SCOPE, a.text)

    if global_hits:
        a.hard_out_of_scope = True
        a.out_of_scope = sorted(set(global_hits) | set(hard_any))
        return a

    if not hard_any:
        intents = _hits(IN_SCOPE, a.text)
    else:
        # clause-level gate: only clauses without a hard out-of-scope signal may contribute intents
        intents: dict[str, int] = {}
        offset = 0
        for clause in re.split(CLAUSE_SPLIT, a.text, flags=re.IGNORECASE):
            if clause is None:
                continue
            start = a.text.find(clause, offset) if clause.strip() else offset
            if clause.strip() and not _hits(OUT_OF_SCOPE, clause):
                for k, pos in _hits(IN_SCOPE, clause).items():
                    intents.setdefault(k, start + pos)
            offset = max(offset, start + len(clause))
    # "where do I register?" is a registration question; 'wapi/where' alone must not add a depot intent
    if "registration_process" in intents and "depot_availability" in intents and not re.search(
        r"\b(depo|depot|kituo|vituo|chukua|nachukua|collect\w*|inanihudumia|stock|imefika)\b", a.text, re.IGNORECASE
    ):
        intents.pop("depot_availability")
    a.intents = [k for k, _ in sorted(intents.items(), key=lambda kv: kv[1])]
    a.out_of_scope = sorted(set(hard_any) | set(soft_hits))
    if hard_any and not a.intents:
        a.hard_out_of_scope = True

    for flag, value, pat in DECLARED:
        if re.search(pat, a.text, re.IGNORECASE):
            a.declared[flag] = value
    # a declaration alone ("sijasajiliwa") implies the registration_process intent
    if not a.intents and not a.hard_out_of_scope and a.declared.get("is_registered_kiamis") is False:
        a.intents.append("registration_process")
    return a


# --- optional Claude Haiku classifier ---------------------------------------
_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "in_scope": {"type": "boolean"},
        "intent": {"type": "string", "enum": list(INTENTS) + ["out_of_scope"]},
        "out_of_scope_reasons": {"type": "array", "items": {"type": "string", "enum": list(OUT_OF_SCOPE) + list(GLOBAL_OUT_OF_SCOPE)}},
        "declared": {
            "type": "object",
            "properties": {f: {"type": ["boolean", "null"]} for f in DECLARED_FLAGS},
            "required": list(DECLARED_FLAGS),
            "additionalProperties": False,
        },
    },
    "required": ["in_scope", "intent", "out_of_scope_reasons", "declared"],
    "additionalProperties": False,
}
_CLASSIFY_SYSTEM = (
    "You classify one SMS from a Kenyan smallholder farmer for Nitapata?, an assistant that answers ONLY "
    "eligibility, registration process, price, depot/collection-point availability, e-voucher redemption and "
    "cycle timing questions about the National Fertiliser Subsidy Programme. Messages mix Kiswahili, Sheng and "
    "English. Anything about agronomy (what/when to plant or apply, crops, pests, weather), loans, insurance, "
    "buying or selling, or a request to look up the farmer's own record is out of scope. If uncertain, mark "
    "out_of_scope. For declared state, set a flag only when the farmer explicitly states it (e.g. 'nina ID' -> "
    "has_id true, 'sijasajiliwa' -> is_registered_kiamis false); otherwise null. Never infer."
)


def refine_with_llm(a: Analysis, client) -> Analysis:
    """Refine intent/declared with Haiku. Rules' out-of-scope hits are kept as a hard gate."""
    if client is None or a.keyword or a.hard_out_of_scope:
        return a
    try:
        import json

        from nitapata.constants import MODEL_ID

        resp = client.with_options(timeout=10.0, max_retries=1).messages.create(
            model=MODEL_ID,
            max_tokens=256,
            system=[{"type": "text", "text": _CLASSIFY_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": a.text}],
            output_config={"format": {"type": "json_schema", "schema": _CLASSIFY_SCHEMA}},
        )
        data = json.loads(next(b.text for b in resp.content if b.type == "text"))
        a.classifier = "haiku"
        if not data["in_scope"] or data["intent"] == "out_of_scope":
            a.intents = []
            a.hard_out_of_scope = True
            a.out_of_scope = sorted(set(a.out_of_scope) | set(data["out_of_scope_reasons"]) or {"other"})
        elif data["intent"] not in a.intents:
            a.intents.insert(0, data["intent"])
        for f, v in data["declared"].items():
            if isinstance(v, bool) and f not in a.declared:
                a.declared[f] = v
    except Exception as exc:  # any API problem -> rules result stands
        log.warning("haiku classify failed, using rules: %s", type(exc).__name__)
    return a
