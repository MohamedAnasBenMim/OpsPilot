"""Rapport d'incident (Markdown) et message de chat."""

from __future__ import annotations

from .models import CATEGORY_LABELS, Diagnosis


def markdown(d: Diagnosis) -> str:
    hyps = "\n".join(f"| {CATEGORY_LABELS[h.category]} | {h.confidence:.0%} | {', '.join(h.evidence) or '—'} |" for h in d.hypotheses)
    ev = "\n".join(f"- `{e.id}` — {e.text}" for e in d.evidence) or "- Aucune preuve collectée"
    return f"""# {d.incident_id} — {d.title}

| | |
|---|---|
| Alerte | `{d.alert.name}` ({d.alert.severity}) |
| Service | `{d.alert.service}` |
| Début | {d.alert.starts_at.isoformat()} |
| Moteur | {d.engine} ({d.duration_ms} ms) |

## Diagnostic

**Cause racine probable : {CATEGORY_LABELS[d.root_cause]}** (confiance {d.confidence:.0%})

{d.summary}

## Action recommandée

{d.remediation}

## Hypothèses

| Cause | Confiance | Preuves |
|---|---|---|
{hyps}

## Preuves collectées

{ev}

## À compléter par l'astreinte

- Chronologie confirmée :
- Impact utilisateurs :
- Actions correctives et préventives :
"""


def chat_message(d: Diagnosis, link: str) -> dict:
    """Payload compatible webhooks entrants Slack et Teams (champ `text`)."""
    top = "\n".join(f"• {e.text}" for e in d.evidence[:3])
    return {
        "text": f":rotating_light: *{d.incident_id}* — {d.title}\n"
        f"Cause probable ({d.confidence:.0%}) : {CATEGORY_LABELS[d.root_cause]}\n{top}\n"
        f"Action : {d.remediation}\nRapport : {link}"
    }
