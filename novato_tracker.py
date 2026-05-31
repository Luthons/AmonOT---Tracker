"""
novato_tracker.py
Verifica novatos que completaram 30 dias e notifica a staff via DM no Discord.
Roda junto com o update_realtime.
"""

import os
import time
import requests
from datetime import datetime, timezone, timedelta

DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
SUPABASE_URL  = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY", "")

SUPA_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}

DISCORD_HEADERS = {
    "Authorization": f"Bot {DISCORD_TOKEN}",
    "Content-Type":  "application/json",
}


def supa_get(table, params):
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=SUPA_HEADERS, params=params, timeout=10)
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        print(f"[novato] erro Supabase: {e}")
        return []


def supa_patch(table, params, body):
    try:
        requests.patch(f"{SUPABASE_URL}/rest/v1/{table}", headers={**SUPA_HEADERS, "Prefer": "return=minimal"}, params=params, json=body, timeout=10)
    except Exception as e:
        print(f"[novato] erro PATCH: {e}")


def get_dm_channel(user_id):
    try:
        r = requests.post("https://discord.com/api/v10/users/@me/channels", headers=DISCORD_HEADERS, json={"recipient_id": user_id}, timeout=10)
        return r.json()["id"] if r.status_code in (200, 201) else None
    except:
        return None


def send_dm(user_id, embed):
    channel_id = get_dm_channel(user_id)
    if not channel_id:
        return False
    for _ in range(3):
        r = requests.post(f"https://discord.com/api/v10/channels/{channel_id}/messages", headers=DISCORD_HEADERS, json={"embeds": [embed]}, timeout=10)
        if r.status_code in (200, 201):
            return True
        if r.status_code == 429:
            time.sleep(r.json().get("retry_after", 1))
        else:
            break
    return False


def run():
    if not DISCORD_TOKEN or not SUPABASE_URL or not SUPABASE_KEY:
        print("[novato] ⚠ configuração incompleta")
        return

    # Busca novatos com 30+ dias que ainda não foram notificados
    threshold = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    novatos = supa_get("profiles", {
        "role":            "eq.novato",
        "novato_since":    f"lte.{threshold}",
        "novato_notified": "eq.false",
        "select":          "id,display_name,novato_since",
    })

    if not novatos:
        print("[novato] nenhum novato com 30+ dias pendente")
        return

    # Busca staff com discord_id
    staff = supa_get("profiles", {"role": "in.(admin,lider,vice)", "select": "discord_id,display_name"})
    staff_ids = [(p["discord_id"], p["display_name"]) for p in staff if p.get("discord_id")]

    for novato in novatos:
        since = novato.get("novato_since", "")[:10]
        embed = {
            "title":       "⏰ Novato completou 30 dias!",
            "description": f"**{novato['display_name']}** está há 30 dias como Novato e pode ser promovido a Membro.",
            "color":       0xC9A84C,
            "fields": [
                {"name": "👤 Player",    "value": novato["display_name"], "inline": True},
                {"name": "📅 Novato desde", "value": since,              "inline": True},
            ],
            "footer": {"text": "Lowly People · Acesse o painel para promover o cargo."},
        }

        for discord_id, staff_name in staff_ids:
            if send_dm(discord_id, embed):
                print(f"[novato] ✅ DM enviada para {staff_name} sobre {novato['display_name']}")
            time.sleep(0.3)

        # Marca como notificado
        supa_patch("profiles", {"id": f"eq.{novato['id']}"}, {"novato_notified": True})
        print(f"[novato] ✅ {novato['display_name']} marcado como notificado")


if __name__ == "__main__":
    run()
