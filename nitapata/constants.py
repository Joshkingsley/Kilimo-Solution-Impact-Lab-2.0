"""Fixed constants. Nothing else in the codebase may compose a refusal (SPEC §4)."""
from __future__ import annotations

import os

CURRENT_CYCLE = os.environ.get("CURRENT_CYCLE", "2026-SR")
CLARIFY_TTL_SECONDS = int(os.environ.get("CLARIFY_TTL_SECONDS", "900"))
MODEL_ID = os.environ.get("NITAPATA_MODEL", "claude-haiku-4-5")
INGEST_VERSION = 1

INTENTS = (
    "eligibility",
    "registration_process",
    "price",
    "depot_availability",
    "evoucher_redemption",
    "cycle_timing",
)
DECLARED_FLAGS = ("has_id", "is_registered_kiamis", "has_allocation_sms", "has_ecitizen_payment")

# --- the only refusal wordings (byte-identical everywhere) -------------------
BOUNDARY_LINE = {
    "sw": "Nitapata? hujibu tu maswali ya sifa, utaratibu na bei ya mbolea ya ruzuku. Hilo liko nje ya wigo wetu.",
    "en": "Nitapata? only answers eligibility, process and price questions about subsidised fertiliser. That is outside our scope.",
}
FALLBACK_LINE = {
    "sw": "Samahani, hili si kwenye hati za umma tulizo nazo. Uliza ofisi ya kilimo ya wodi yako.",
    "en": "Sorry, that is not in the public documents we hold. Ask your ward agricultural office.",
}
# --- keyword replies (SPEC §9.6), also boundary outcome ----------------------
HELP_LINE = {  # one GSM-7 segment each (<=160)
    "sw": "Nitapata? hujibu maswali ya sifa, utaratibu na bei ya mbolea ya ruzuku kutoka hati za umma pekee. Si ushauri wa kilimo, mkopo au bima. Tuma swali na kaunti.",
    "en": "Nitapata? answers eligibility, process and price questions on subsidised fertiliser from public documents only. No farming advice, loans or insurance.",
}
STOP_LINE = {
    "sw": "Sawa. Hatuhifadhi chochote kukuhusu; mazungumzo yamefutwa.",
    "en": "Done. We keep nothing about you; this conversation has been wiped.",
}
LANG_PIN_LINE = {
    "sw": "Sawa, tutajibu kwa Kiswahili.",
    "en": "OK, we will reply in English.",
}
CLARIFY_COUNTY = {
    "sw": "Uko kaunti gani? (mf. Kakamega, Machakos) Bei na vituo hutegemea kaunti.",
    "en": "Which county are you in? (e.g. Kakamega, Machakos) Price and collection points depend on the county.",
}
CLARIFY_INTENT = {
    "sw": "Unauliza kuhusu bei, sifa, usajili au vituo vya mbolea ya ruzuku? Taja kaunti yako.",
    "en": "Are you asking about price, eligibility, registration or collection points for subsidised fertiliser? Say your county.",
}

FIXED_LINES = {*BOUNDARY_LINE.values(), *FALLBACK_LINE.values(), *HELP_LINE.values(),
               *STOP_LINE.values(), *LANG_PIN_LINE.values()}

# Component labels for the frozen template (SPEC §9.5)
LABEL_REQ = {"sw": "Unahitaji:", "en": "You need:"}
LABEL_GAP = {"sw": "Ulisema huna:", "en": "You said you lack:"}
LABEL_DECLINE = {"sw": "Si kwenye hati:", "en": "Not in the documents:"}

FLAG_LABELS = {
    "has_id": {"sw": "kitambulisho asili", "en": "original national ID"},
    "is_registered_kiamis": {"sw": "jina kwenye rejista ya wakulima", "en": "name on the farmers' register"},
    "has_allocation_sms": {"sw": "SMS ya mgao", "en": "allocation SMS"},
    "has_ecitizen_payment": {"sw": "malipo ya eCitizen", "en": "eCitizen payment"},
}

GSM7_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM7_EXT = set("^{}\\[~]|€")
