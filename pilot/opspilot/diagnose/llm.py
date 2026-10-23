"""Diagnostic par LLM (API compatible OpenAI : OpenAI, Mistral, Groq, Ollama...).

Le LLM reçoit les preuves identifiées et les hypothèses du moteur à règles, et doit
répondre en JSON en citant les identifiants de preuves. Toute réponse invalide
déclenche un repli sur le moteur à règles.
"""

from __future__ import annotations

import json
import re

import httpx

from ..config import settings
from ..models import Category, Hypothesis, Snapshot
from ..signals import Signals

SYSTEM = """Tu es un ingénieur SRE d'astreinte. À partir des preuves fournies, identifie la cause racine de l'incident.
Catégories autorisées : {cats}.
Réponds UNIQUEMENT en JSON : {{"root_cause": "<catégorie>", "confidence": <0..1>, "evidence": ["<id>", ...],
"summary": "<2 phrases en français, factuelles, qui citent les chiffres>", "alternatives": ["<catégorie>", ...]}}
N'utilise que des identifiants de preuves présents dans la liste. Méfie-toi des fausses pistes :
un déploiement récent n'est pas forcément la cause."""


def build_prompt(snap: Snapshot, sig: Signals, baseline: list[Hypothesis]) -> str:
    ev = "\n".join(f"- [{e.id}] ({e.kind}) {e.text}" for e in sig.evidence) or "- (aucune preuve notable)"
    hints = ", ".join(f"{h.category.value}={h.confidence:.2f}" for h in baseline[:3])
    return (
        f"Alerte : {snap.alert.name} sur le service {snap.alert.service} (sévérité {snap.alert.severity}).\n"
        f"{snap.alert.summary}\n\nPreuves :\n{ev}\n\nScores du moteur à règles (indicatif) : {hints}"
    )


def parse(text: str, valid_ids: set[str]) -> tuple[Hypothesis, list[Hypothesis], str]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("pas de JSON dans la réponse")
    data = json.loads(m.group(0))
    cat = Category(data["root_cause"])
    evidence = [e for e in data.get("evidence", []) if e in valid_ids]
    main = Hypothesis(category=cat, confidence=float(data.get("confidence", 0.5)), evidence=evidence)
    alts = [Hypothesis(category=Category(a), confidence=0.0) for a in data.get("alternatives", []) if a in Category._value2member_map_]
    return main, alts, str(data.get("summary", ""))


def diagnose_llm(snap: Snapshot, sig: Signals, baseline: list[Hypothesis]) -> tuple[list[Hypothesis], str]:
    cats = ", ".join(c.value for c in Category)
    resp = httpx.post(
        f"{settings.llm_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.llm_api_key}"},
        json={
            "model": settings.llm_model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM.format(cats=cats)},
                {"role": "user", "content": build_prompt(snap, sig, baseline)},
            ],
        },
        timeout=60,
    )
    resp.raise_for_status()
    main, alts, summary = parse(resp.json()["choices"][0]["message"]["content"], {e.id for e in sig.evidence})
    return [main, *[a for a in alts if a.category != main.category]], summary
