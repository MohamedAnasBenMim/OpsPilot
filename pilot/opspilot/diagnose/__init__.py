"""Point d'entrée du diagnostic : snapshot -> Diagnosis."""

from __future__ import annotations

import hashlib
import logging
import time

from ..config import settings
from ..models import CATEGORY_LABELS, REMEDIATIONS, Category, Diagnosis, Snapshot
from ..signals import extract
from .rules import score

log = logging.getLogger(__name__)


def _summary(top_cat: Category, ev_text: list[str]) -> str:
    if top_cat == Category.UNKNOWN:
        return "Aucun signal assez fort pour conclure ; voir les preuves collectées."
    lead = CATEGORY_LABELS[top_cat]
    return f"{lead}. " + " ; ".join(ev_text[:3]) + "."


def diagnose(snap: Snapshot, engine: str | None = None) -> Diagnosis:
    t0 = time.perf_counter()
    engine = engine or ("llm" if settings.llm_api_key else "rules")
    sig = extract(snap)
    hyps = score(sig)
    summary = ""
    used = "rules"
    if engine == "llm":
        try:
            from .llm import diagnose_llm

            hyps, summary = diagnose_llm(snap, sig, hyps)
            used = f"llm:{settings.llm_model}"
        except Exception as exc:  # noqa: BLE001 — repli volontaire sur les règles
            log.warning("diagnostic LLM indisponible, repli sur les règles : %s", exc)

    top = hyps[0]
    by_id = {e.id: e for e in sig.evidence}
    cited = [by_id[i] for i in top.evidence if i in by_id]
    if not summary:
        summary = _summary(top.category, [e.text for e in cited])
    key = f"{snap.alert.service}:{snap.alert.name}:{snap.alert.starts_at.isoformat()}"
    return Diagnosis(
        incident_id="INC-" + hashlib.sha1(key.encode()).hexdigest()[:8].upper(),
        alert=snap.alert,
        root_cause=top.category,
        title=f"{CATEGORY_LABELS[top.category]} — {snap.alert.service}",
        summary=summary,
        confidence=top.confidence,
        hypotheses=hyps[:4],
        evidence=cited + [e for e in sig.evidence if e not in cited],
        remediation=REMEDIATIONS[top.category],
        engine=used,
        duration_ms=round((time.perf_counter() - t0) * 1000, 2),
    )
