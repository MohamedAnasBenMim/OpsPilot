"""Configuration par variables d'environnement."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    # Sources de télémétrie (mode live)
    prometheus_url: str = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
    loki_url: str = os.getenv("LOKI_URL", "")  # vide : pas de logs
    # sélecteur de flux Loki ; $SVC est remplacé par le nom du service
    loki_selector: str = os.getenv("LOKI_SELECTOR", '{service="$SVC"}')
    kubernetes_api: str = os.getenv("KUBERNETES_API", "")  # vide : détection in-cluster automatique
    namespace: str = os.getenv("TARGET_NAMESPACE", "shop")
    lookback_minutes: int = int(os.getenv("LOOKBACK_MINUTES", "40"))
    # LLM compatible OpenAI ; sans clé, moteur à règles
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_api_key: str | None = os.getenv("LLM_API_KEY")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    # Notification (Slack ou Teams, webhook entrant)
    chat_webhook_url: str | None = os.getenv("CHAT_WEBHOOK_URL")
    public_url: str = os.getenv("PUBLIC_URL", "http://localhost:8080")


settings = Settings()
