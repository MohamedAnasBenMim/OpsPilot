"""Évalue le diagnostic sur les scénarios enregistrés.

Métriques :
- top-1      : la cause racine principale est la bonne
- top-3      : la bonne cause figure parmi les 3 premières hypothèses
- preuves    : part des preuves clés (vérité terrain) citées par l'hypothèse retenue
- latence    : durée médiane du diagnostic (collecte exclue)

    python eval/run_eval.py                         # scénarios synthétiques, moteur par défaut
    python eval/run_eval.py --engine rules --min-top1 0.8
    python eval/run_eval.py --scenarios eval/real   # vrais incidents enregistrés
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

from opspilot.diagnose import diagnose
from opspilot.sources.fixture import load

HERE = Path(__file__).parent


def evaluate(scenario_dir: Path, engine: str | None) -> dict:
    rows = []
    for path in sorted(scenario_dir.glob("*.json")):
        snap = load(path)
        gt = snap.ground_truth or {}
        if gt.get("category") in (None, "TODO"):
            continue
        d = diagnose(snap, engine=engine)
        cats = [h.category.value for h in d.hypotheses]
        top = d.hypotheses[0]
        keys = set(gt.get("key_evidence", []))
        rows.append(
            {
                "id": snap.id,
                "expected": gt["category"],
                "predicted": d.root_cause.value,
                "top1": d.root_cause.value == gt["category"],
                "top3": gt["category"] in cats[:3],
                "evidence_recall": (len(keys & set(top.evidence)) / len(keys)) if keys else None,
                "confidence": d.confidence,
                "ms": d.duration_ms,
                "engine": d.engine,
            }
        )
    if not rows:
        raise SystemExit(f"aucun scénario annoté dans {scenario_dir}")
    per_cat: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        per_cat[r["expected"]].append(r["top1"])
    recalls = [r["evidence_recall"] for r in rows if r["evidence_recall"] is not None]
    return {
        "engine": rows[0]["engine"],
        "n": len(rows),
        "top1": sum(r["top1"] for r in rows) / len(rows),
        "top3": sum(r["top3"] for r in rows) / len(rows),
        "evidence_recall": statistics.mean(recalls) if recalls else None,
        "latency_ms_p50": statistics.median(r["ms"] for r in rows),
        "per_category": {c: sum(v) / len(v) for c, v in sorted(per_cat.items())},
        "rows": rows,
    }


def to_markdown(res: dict, source: str) -> str:
    lines = [
        f"# Évaluation du diagnostic — {source}\n",
        f"Moteur : `{res['engine']}` · {res['n']} incidents\n",
        "| Top-1 | Top-3 | Preuves clés citées | Latence p50 |",
        "|---|---|---|---|",
        f"| {res['top1']:.0%} | {res['top3']:.0%} | {res['evidence_recall']:.0%} | {res['latency_ms_p50']:.1f} ms |\n",
        "| Cause racine | Top-1 |",
        "|---|---|",
    ]
    lines += [f"| {c} | {v:.0%} |" for c, v in res["per_category"].items()]
    errors = [r for r in res["rows"] if not r["top1"]]
    if errors:
        lines += ["\n## Erreurs", "", "| Scénario | Attendu | Prédit |", "|---|---|---|"]
        lines += [f"| {r['id']} | {r['expected']} | {r['predicted']} |" for r in errors]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", type=Path, default=HERE / "scenarios")
    ap.add_argument("--engine", choices=["rules", "llm"], default=None)
    ap.add_argument("--out", type=Path, default=HERE / "results")
    ap.add_argument("--min-top1", type=float, default=None)
    args = ap.parse_args()

    res = evaluate(args.scenarios, args.engine)
    md = to_markdown(res, "scénarios synthétiques" if args.scenarios.name == "scenarios" else str(args.scenarios))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.md").write_text(md, encoding="utf-8")
    (args.out / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(md)
    if args.min_top1 is not None and res["top1"] < args.min_top1:
        raise SystemExit(f"top-1 {res['top1']:.0%} < seuil {args.min_top1:.0%}")


if __name__ == "__main__":
    main()
