"""
activity_log.py
Helper para registrar ações no log de atividades.
Usado pelos scripts Python e importado onde necessário.
"""

import os
import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

SUPA_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=minimal",
}


def log(actor: str, action: str, category: str, details: dict = None):
    """
    Registra uma ação no log de atividades.

    actor    — quem fez (display_name ou 'Sistema')
    action   — descrição legível ex: "Nick atualizado: Darkzao Knight → MentaliTY"
    category — hunted | bonus | raffle | shop | admin | invite | calendar | enemy_guild | nick | death
    details  — dict com dados extras (opcional)
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return

    payload = {
        "actor":    actor,
        "action":   action,
        "category": category,
        "details":  details or {},
    }

    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/activity_log",
            headers=SUPA_HEADERS,
            json=payload,
            timeout=10,
        )
    except Exception as e:
        print(f"[activity_log] erro ao registrar log: {e}")
