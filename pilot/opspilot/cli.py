"""Ligne de commande.

opspilot diagnose eval/scenarios/s01_bad_deploy.json      # rejouer un incident enregistré
opspilot record --service orders-api --alert OrdersHighErrorRate --out eval/real/inc-01.json
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

import httpx

from .diagnose import diagnose
from .models import Alert, Deploy
from .report import markdown
from .sources import fixture, live


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="opspilot")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("diagnose", help="diagnostiquer un snapshot JSON")
    d.add_argument("snapshot")
    d.add_argument("--engine", choices=["rules", "llm"], default=None)

    r = sub.add_parser("record", help="enregistrer la télémétrie live d'un incident dans un snapshot JSON")
    r.add_argument("--service", required=True)
    r.add_argument("--alert", default="ManualCapture")
    r.add_argument("--starts-at", default=None, help="ISO 8601, par défaut maintenant")
    r.add_argument("--out", required=True)
    r.add_argument("--opspilot-url", default=None, help="récupère aussi les déploiements enregistrés via POST /deploys")

    args = ap.parse_args(argv)
    if args.cmd == "diagnose":
        print(markdown(diagnose(fixture.load(args.snapshot), engine=args.engine)))
    else:
        starts = datetime.fromisoformat(args.starts_at) if args.starts_at else datetime.now(UTC)
        deploys = []
        if args.opspilot_url:
            deploys = [Deploy.model_validate(d) for d in httpx.get(f"{args.opspilot_url}/deploys", timeout=10).json()]
        snap = live.collect(Alert(name=args.alert, service=args.service, starts_at=starts), deploys)
        snap.ground_truth = {"category": "TODO", "key_evidence": []}
        fixture.save(snap, args.out)
        print(f"Snapshot écrit dans {args.out} : complète 'ground_truth' après le post-mortem.")


if __name__ == "__main__":
    main()
