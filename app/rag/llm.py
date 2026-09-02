"""LLM access. Two jobs (SPEC §9.3): classification (scope gate + intent + declared
state + county/depot) and generation (cite-or-refuse structured draft).

`AnthropicLLM` calls Claude with structured output and a cached system prompt.
`FakeLLM` is a deterministic rule-based stand-in for tests, CI and offline demos.
The pipeline only ever sees the `Classification` / `Draft` models, so the two are
interchangeable and the guardrails run identically on both.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.rag import geo
from app.rag.config import Settings
from app.rag.prompts import CLASSIFY_SYSTEM, GENERATE_SYSTEM
from app.rag.schema import DECLARED_FLAGS, INTENTS, Chunk, DeclaredFlag, IntentOrOOS, Lang

log = logging.getLogger("nitapata.llm")


# ------------------------------------------------------------------ shapes
class SubAsk(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = Field(max_length=500)
    intent: IntentOrOOS
    in_scope: bool


class Classification(BaseModel):
    model_config = ConfigDict(extra="ignore")
    language: Lang = "sw"
    sub_asks: list[SubAsk] = Field(default_factory=list)
    county: str | None = None
    depot: str | None = None
    declared: dict[DeclaredFlag, bool] = Field(default_factory=dict)
    personal_record_claims: list[str] = Field(default_factory=list)
    trend_claims: list[str] = Field(default_factory=list)
    retrieval_query_en: str = ""

    @property
    def in_scope_asks(self) -> list[SubAsk]:
        return [a for a in self.sub_asks if a.in_scope and a.intent != "out_of_scope"]

    @property
    def out_of_scope_asks(self) -> list[SubAsk]:
        return [a for a in self.sub_asks if not a.in_scope or a.intent == "out_of_scope"]

    @property
    def primary_intent(self) -> IntentOrOOS:
        asks = self.in_scope_asks
        return asks[0].intent if asks else "out_of_scope"


class DraftRequirement(BaseModel):
    model_config = ConfigDict(extra="ignore")
    flag: DeclaredFlag
    label_en: str = Field(max_length=60)
    label_sw: str = Field(max_length=60)
    chunk_id: str


class Draft(BaseModel):
    model_config = ConfigDict(extra="ignore")
    insufficient: bool = False
    answer: str = Field(default="", max_length=600)
    answer_chunk_ids: list[str] = Field(default_factory=list)
    requirements: list[DraftRequirement] = Field(default_factory=list)
    declines: list[str] = Field(default_factory=list)


class LLMUsage(BaseModel):
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class LLM(Protocol):
    name: str

    def classify(self, text: str) -> tuple[Classification, LLMUsage]: ...
    def generate(self, asks: list[str], chunks: list[Chunk], language: Lang) -> tuple[Draft, LLMUsage]: ...


# ------------------------------------------------------------------ JSON schemas (closed enums)
_INTENT_ENUM = list(INTENTS) + ["out_of_scope"]
_FLAG_ENUM = list(DECLARED_FLAGS)

CLASSIFY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["language", "sub_asks", "county", "depot", "declared", "personal_record_claims", "trend_claims", "retrieval_query_en"],
    "properties": {
        "language": {"type": "string", "enum": ["en", "sw"]},
        "sub_asks": {"type": "array", "maxItems": 5, "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "intent", "in_scope"],
            "properties": {"text": {"type": "string"}, "intent": {"type": "string", "enum": _INTENT_ENUM},
                           "in_scope": {"type": "boolean"}}}},
        "county": {"type": ["string", "null"]},
        "depot": {"type": ["string", "null"]},
        "declared": {"type": "object", "additionalProperties": False,
                     "properties": {f: {"type": "boolean"} for f in _FLAG_ENUM}},
        "personal_record_claims": {"type": "array", "items": {"type": "string"}},
        "trend_claims": {"type": "array", "items": {"type": "string"}},
        "retrieval_query_en": {"type": "string"},
    },
}

GENERATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["insufficient", "answer", "answer_chunk_ids", "requirements", "declines"],
    "properties": {
        "insufficient": {"type": "boolean"},
        "answer": {"type": "string"},
        "answer_chunk_ids": {"type": "array", "items": {"type": "string"}},
        "requirements": {"type": "array", "maxItems": 4, "items": {
            "type": "object", "additionalProperties": False, "required": ["flag", "label_en", "label_sw", "chunk_id"],
            "properties": {"flag": {"type": "string", "enum": _FLAG_ENUM}, "label_en": {"type": "string"},
                           "label_sw": {"type": "string"}, "chunk_id": {"type": "string"}}}},
        "declines": {"type": "array", "maxItems": 5, "items": {"type": "string"}},
    },
}


def _escape(text: str) -> str:
    """Neutralise tag-like sequences so document/farmer text cannot close our wrappers."""
    return text.replace("<", "‹").replace(">", "›")


def render_chunks(chunks: list[Chunk]) -> str:
    parts = []
    for i, c in enumerate(chunks, start=1):
        parts.append(
            f'<chunk n="{i}" chunk_id="{c.chunk_id}" doc="{_escape(c.doc_title)}" page="{c.page_label}" '
            f'published="{c.publish_date.isoformat()}" authority="{c.authority}" cycle="{c.cycle or "any"}">\n'
            f"{_escape(c.text)}\n</chunk>"
        )
    return "\n".join(parts)


# ------------------------------------------------------------------ Anthropic
class AnthropicLLM:
    def __init__(self, settings: Settings):
        import anthropic  # lazy: tests never import the SDK client

        kwargs = {"timeout": settings.llm_timeout_seconds, "max_retries": settings.llm_max_retries}
        if settings.anthropic_api_key:
            kwargs["api_key"] = settings.anthropic_api_key
        self._client = anthropic.Anthropic(**kwargs)
        self._anthropic = anthropic
        self.model = settings.anthropic_model
        self.name = f"anthropic:{self.model}"
        self.s = settings

    def _call(self, system: str, user: str, schema: dict, max_tokens: int) -> tuple[dict, LLMUsage]:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            # stable prefix first, with the cache breakpoint; volatile content only in `messages`
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        usage = LLMUsage(
            model=resp.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("model refused")
        text = "".join(b.text for b in resp.content if b.type == "text")
        return json.loads(text), usage

    def classify(self, text: str) -> tuple[Classification, LLMUsage]:
        user = f"<farmer_message>\n{_escape(text)}\n</farmer_message>"
        data, usage = self._call(CLASSIFY_SYSTEM, user, CLASSIFY_SCHEMA, self.s.classification_max_tokens)
        try:
            cls = Classification.model_validate(data)
        except ValidationError as exc:
            log.warning("classification failed validation: %s", exc)
            cls = Classification(language="sw", sub_asks=[])          # unparseable → out of scope
        return cls, usage

    def generate(self, asks: list[str], chunks: list[Chunk], language: Lang) -> tuple[Draft, LLMUsage]:
        user = (
            f"<retrieved_chunks>\n{render_chunks(chunks)}\n</retrieved_chunks>\n\n"
            f"<farmer_questions language=\"{language}\">\n"
            + "\n".join(f"- {_escape(a)}" for a in asks)
            + "\n</farmer_questions>\n\nReply language: " + ("Kiswahili" if language == "sw" else "English")
        )
        data, usage = self._call(GENERATE_SYSTEM, user, GENERATE_SCHEMA, self.s.generation_max_tokens)
        try:
            draft = Draft.model_validate(data)
        except ValidationError as exc:
            log.warning("draft failed validation: %s", exc)
            draft = Draft(insufficient=True)
        return draft, usage


# ------------------------------------------------------------------ Fake (rules)
_OOS = re.compile(
    r"\b(loan|mkopo|credit|insurance|bima|plant\w*|panda|kupanda|inakauka|kauka|drying|yield|mavuno|harvest|"
    r"sell|uza|kuuza|buy|nunua|market|soko|pesticide|dawa|seed|mbegu|weather|hali ya hewa)\b", re.I)
_INTENT_RULES: list[tuple[str, re.Pattern]] = [
    ("evoucher_redemption", re.compile(r"\b(voucher|e-voucher|redeem|nikuje na|niende na|bring|nini.*depot|kuchukua|collect)\b", re.I)),
    ("registration_process", re.compile(r"\b(regist\w*|kiamis|sajili\w*|usajili|kujisajili|step|hatua)\b", re.I)),
    ("price", re.compile(r"\b(bei|price|cost|gharama|ngapi|ksh|shilingi|bag|gunia)\b", re.I)),
    ("depot_availability", re.compile(r"\b(depot|depo|ghala|stock|bado\??|available|ncpb)\b", re.I)),
    ("cycle_timing", re.compile(r"\b(msimu|season|cycle|lini|when|inaanza|start\w*|deadline)\b", re.I)),
    ("eligibility", re.compile(r"\b(naweza pata|eligib\w*|qualif\w*|nastahili|ustahiki|sifa|nahitaji nini|ninahitaji|what do i need|who can|nani)\b", re.I)),
]
_PERSONAL = re.compile(r"\b(nilikuwa na shida na regist|am i regist|nimesajiliwa\??|my registration|registration last time|"
                       r"nimesajiliwa|sijasajiliwa\?|niko kwenye|my record)\b", re.I)
_TREND = re.compile(r"\b(imepanda|imeshuka|gone up|gone down|increased|decreased|changed|imebadilika|kupanda|trend)\b", re.I)
_DECL = [
    ("has_id", True, re.compile(r"\b(nina (id|kitambulisho)|have (my |an? )?(id|national id)|niko na id)\b", re.I)),
    ("has_id", False, re.compile(r"\b(sina (id|kitambulisho)|no id|don'?t have (my |an? )?(id|national id)|lost my id)\b", re.I)),
    ("has_allocation_sms", True, re.compile(r"\b(nimepata sms|got (the |my )?(allocation )?sms|nina sms)\b", re.I)),
    ("has_allocation_sms", False, re.compile(r"\b(sijapata sms|sina sms|no sms|not received (the |any )?sms|haven'?t (got|received) (the |an? )?sms)\b", re.I)),
    ("is_registered_kiamis", True, re.compile(r"\b(nimesajiliwa kiamis|i am registered|nimejisajili|am registered on kiamis)\b", re.I)),
    ("is_registered_kiamis", False, re.compile(r"\b(sijasajiliwa|sijajisajili|not registered|haven'?t registered)\b", re.I)),
    ("has_ecitizen_payment", True, re.compile(r"\b(nimelipa|have paid|nililipa ecitizen|paid on ecitizen)\b", re.I)),
    ("has_ecitizen_payment", False, re.compile(r"\b(sijalipa|have not paid|haven'?t paid|not paid)\b", re.I)),
]
_SW_MARKERS = re.compile(r"\b(na|ya|ni|bei|mbolea|naweza|nini|lini|bado|sawa|niaje|sina|nina|kwa|wa|za|ama|je|ngapi|depo|"
                         r"ruzuku|nahitaji|nipate|imepanda|kuliko|mwaka|jana|inanihudumia|msimu|mkopo|bima|wapi|gani|"
                         r"kitambulisho|usajili|sajili|nikienda|nikuje|hii|hilo|yangu|mahindi|nifanye|mbegu)\b", re.I)
_EN_MARKERS = re.compile(r"\b(the|is|are|what|how|much|price|which|do|does|i|my|can|where|when|need|have|depot|"
                         r"register|registered|fertiliser|fertilizer|bag|county|you|your|please|tell|me)\b", re.I)
_FLAG_KEYWORDS: dict[str, tuple[str, ...]] = {
    "has_id": ("national id", "national identity", "identity card", "kitambulisho", " id "),
    "is_registered_kiamis": ("kiamis", "registered", "registration", "usajili"),
    "has_allocation_sms": ("allocation sms", "sms", "e-voucher", "voucher", "mgao"),
    "has_ecitizen_payment": ("ecitizen", "e-citizen", "e citizen"),
}
_EXPAND = {
    "bei": ("price", "ksh", "kshs", "pay"), "mbolea": ("fertilizer", "fertiliser"), "depo": ("depot",), "depot": ("depot", "depots"),
    "sajili": ("register", "registered"), "usajili": ("register", "registered", "registration"), "kitambulisho": ("identity", "id"),
    "nini": ("need", "what"), "nahitaji": ("need", "required"), "nikuje": ("bring", "need", "present"), "gunia": ("bag", "bags"),
    "mgao": ("allocation", "voucher"), "ngapi": ("price", "many", "much"), "lini": ("when", "open", "time"), "wapi": ("where", "nearest"),
    "ruzuku": ("subsidized", "subsidised", "subsidy"), "malipo": ("payment", "pay"), "lipa": ("pay", "payment"),
    "inanihudumia": ("serve", "nearest", "collection", "depot"), "msimu": ("season",),
}
_FLAG_LABELS = {
    "has_id": ("national ID", "kitambulisho"),
    "is_registered_kiamis": ("KIAMIS registration", "usajili wa KIAMIS"),
    "has_allocation_sms": ("allocation SMS", "SMS ya mgao"),
    "has_ecitizen_payment": ("eCitizen payment", "malipo ya eCitizen"),
}


class FakeLLM:
    """Deterministic rules. Exists so the pipeline, guardrails and API are testable
    with no network and no key. Not a product component."""

    name = "fake:rules-v1"

    def classify(self, text: str) -> tuple[Classification, LLMUsage]:
        sw_n, en_n = len(_SW_MARKERS.findall(text)), len(_EN_MARKERS.findall(text))
        lang: Lang = "sw" if sw_n and sw_n >= en_n else "en"
        pieces = [p.strip(" .!") for p in re.split(r"\?|(?:\bna pia\b)|(?:\band also\b)|;", text) if p and p.strip(" .!?")]
        pieces = pieces or [text]
        asks: list[SubAsk] = []
        for p in pieces:
            has_intent = any(rx.search(p) for _, rx in _INTENT_RULES)
            if not has_intent and (_PERSONAL.search(p) or any(rx.search(p) for _, _, rx in _DECL)):
                continue                                       # pure personal claim / declaration → not an ask
            if _OOS.search(p):
                asks.append(SubAsk(text=p[:500], intent="out_of_scope", in_scope=False))
                continue
            intent = next((name for name, rx in _INTENT_RULES if rx.search(p)), None)
            if intent is None:
                asks.append(SubAsk(text=p[:500], intent="out_of_scope", in_scope=False))
            else:
                asks.append(SubAsk(text=p[:500], intent=intent, in_scope=True))
        county, depot = geo.find_in_text(text)
        declared: dict[str, bool] = {}
        for flag, val, rx in _DECL:
            if rx.search(text):
                declared[flag] = val
        personal = [m.group(0) for m in _PERSONAL.finditer(text)]
        trend = [m.group(0) for m in _TREND.finditer(text)]
        in_scope = [a for a in asks if a.in_scope]
        en_terms = []
        for a in in_scope:
            en_terms.append({"price": "subsidised fertiliser price per 50kg bag", "depot_availability": "NCPB depot serving county",
                             "registration_process": "KIAMIS farmer registration steps", "evoucher_redemption": "e-voucher redemption what to bring depot",
                             "eligibility": "who is eligible for subsidised fertiliser", "cycle_timing": "fertiliser subsidy cycle start date"}[a.intent])
        cls = Classification(language=lang, sub_asks=asks, county=county, depot=depot, declared=declared,
                             personal_record_claims=personal, trend_claims=trend, retrieval_query_en=" ; ".join(en_terms))
        return cls, LLMUsage(model=self.name)

    def generate(self, asks: list[str], chunks: list[Chunk], language: Lang) -> tuple[Draft, LLMUsage]:
        if not chunks:
            return Draft(insufficient=True), LLMUsage(model=self.name)
        qtok = {t for a in asks for t in re.findall(r"[a-z0-9]+", a.lower()) if len(t) > 2}
        for a in asks:                                   # bilingual expansion so Kiswahili asks hit English chunks
            for t in re.findall(r"[a-z0-9]+", a.lower()):
                qtok.update(_EXPAND.get(t, ()))
        best_sent, best_score, best_chunk = "", -1.0, None
        if any(c.authority != "secondary" for c in chunks):
            chunks = [c for c in chunks if c.authority != "secondary"]   # never quote a secondary source when better exists
        for rank, c in enumerate(chunks):
            for sent in re.split(r"(?<=[.!?])\s+|\n", c.text):
                s_ = sent.strip()
                words = re.findall(r"[A-Za-z0-9']+", s_)
                if len(words) < 6 or sum(w.isupper() for w in words) > len(words) / 2:
                    continue                             # headings, column-title fragments
                if re.fullmatch(r"\d+\.?", s_) or s_.endswith("?"):
                    continue                                 # numbering, FAQ question lines
                overlap = len(qtok & {w.lower() for w in words})
                score = (2 * overlap + (0.5 if re.search(r"\d", s_) else 0) + (0.5 if c.authority in ("primary", "legal_basis") else 0)
                         + max(0.0, 1.5 - 0.5 * rank))                 # retrieval rank carries information
                if score > best_score:
                    best_sent, best_score, best_chunk = s_, score, c
        if best_chunk is None:
            return Draft(insufficient=True), LLMUsage(model=self.name)
        reqs: list[DraftRequirement] = []
        for c in chunks:
            low = " " + c.text.lower() + " "
            if not re.search(r"\b(must|need|required|bring|present|unahitaji|lazima|leta)\b", low):
                continue
            for flag, kws in _FLAG_KEYWORDS.items():
                if any(k in low for k in kws) and all(r.flag != flag for r in reqs):
                    en, sw = _FLAG_LABELS[flag]
                    reqs.append(DraftRequirement(flag=flag, label_en=en, label_sw=sw, chunk_id=c.chunk_id))
        return Draft(insufficient=False, answer=best_sent[:600], answer_chunk_ids=[best_chunk.chunk_id],
                     requirements=reqs, declines=[]), LLMUsage(model=self.name)


def build_llm(settings: Settings) -> LLM:
    if settings.llm_provider == "fake":
        return FakeLLM()
    return AnthropicLLM(settings)
