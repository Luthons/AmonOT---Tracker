"""
discord_notifier.py
Verifica novos avisos e votações com discord_notify=true
e envia @everyone no canal de anúncios.
"""

import os
import time
import requests
from datetime import datetime, timedelta, timezone

DISCORD_TOKEN    = os.environ.get("DISCORD_BOT_TOKEN", "")
ANNOUNCE_CHANNEL = os.environ.get("DISCORD_ANNOUNCE_CHANNEL_ID", "")
SUPABASE_URL     = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY     = os.environ.get("SUPABASE_SERVICE_KEY", "")

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
        print(f"[discord_notifier] erro: {e}")
        return []


def supa_patch(table, params, body):
    try:
        requests.patch(f"{SUPABASE_URL}/rest/v1/{table}", headers={**SUPA_HEADERS, "Prefer": "return=minimal"}, params=params, json=body, timeout=10)
    except Exception as e:
        print(f"[discord_notifier] erro PATCH: {e}")


def send_channel_message(content, embed=None):
    if not ANNOUNCE_CHANNEL:
        print("[discord_notifier] ⚠ DISCORD_ANNOUNCE_CHANNEL_ID não configurado")
        return False
    payload = {"content": content}
    if embed:
        payload["embeds"] = [embed]
    try:
        r = requests.post(
            f"https://discord.com/api/v10/channels/{ANNOUNCE_CHANNEL}/messages",
            headers=DISCORD_HEADERS,
            json=payload,
            timeout=10,
        )
        return r.status_code in (200, 201)
    except Exception as e:
        print(f"[discord_notifier] erro ao enviar: {e}")
        return False


def get_dm_channel(user_id):
    r = requests.post(
        "https://discord.com/api/v10/users/@me/channels",
        headers=DISCORD_HEADERS,
        json={"recipient_id": user_id},
        timeout=10,
    )
    if r.status_code in (200, 201):
        return r.json()["id"]
    return None


def send_dm(user_id, embed):
    channel_id = get_dm_channel(user_id)
    if not channel_id:
        return False
    r = requests.post(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        headers=DISCORD_HEADERS,
        json={"embeds": [embed]},
        timeout=10,
    )
    return r.status_code in (200, 201)


def notify_new_announcements_dm():
    """
    Busca avisos criados nas últimas 2 horas com notify_groups não vazio
    e envia DM individual para membros com as preferências correspondentes.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

    announcements = supa_get("announcements", {
        "created_at": f"gte.{cutoff}",
        "select":     "id,title,content,notify_groups",
        "limit":      "10",
    })
    announcements = [a for a in announcements if a.get("notify_groups")]

    if not announcements:
        return

    profiles = supa_get("profiles", {
        "select": "id,discord_id,game_preferences,display_name",
        "limit":  "500",
    })

    for ann in announcements:
        notify_groups = ann.get("notify_groups", [])
        ann_id = ann["id"]

        targets = [
            p for p in profiles
            if p.get("discord_id")
            and any(g in (p.get("game_preferences") or []) for g in notify_groups)
        ]

        print(f"[discord_notifier] aviso '{ann['title']}' → {len(targets)} membro(s) a notificar por DM")

        embed = {
            "title":       f"📢 {ann['title']}",
            "description": ann.get("content", ""),
            "color":       0xC9A84C,
            "footer":      {"text": "Lowly People · Mural de Avisos"},
        }

        for profile in targets:
            pid        = profile["id"]
            discord_id = profile["discord_id"]

            sent = supa_get("announcement_notify_sent", {
                "announcement_id": f"eq.{ann_id}",
                "profile_id":      f"eq.{pid}",
                "select":          "announcement_id",
            })
            if sent:
                continue

            if send_dm(discord_id, embed):
                try:
                    requests.post(
                        f"{SUPABASE_URL}/rest/v1/announcement_notify_sent",
                        headers={**SUPA_HEADERS, "Prefer": "resolution=ignore-duplicates,return=minimal"},
                        json={"announcement_id": ann_id, "profile_id": pid},
                        timeout=10,
                    )
                    print(f"[discord_notifier] ✅ DM aviso → {profile.get('display_name', '?')}")
                except Exception as e:
                    print(f"[discord_notifier] erro mark sent: {e}")

            time.sleep(0.3)


def run():
    if not DISCORD_TOKEN or not SUPABASE_URL or not SUPABASE_KEY:
        print("[discord_notifier] ⚠ configuração incompleta")
        return

    # Verifica avisos com discord_notify=true e discord_sent=false
    announcements = supa_get("announcements", {
        "discord_notify": "eq.true",
        "discord_sent":   "eq.false",
        "select":         "id,title,content",
        "limit":          "10",
    })

    for ann in announcements:
        embed = {
            "title":       f"📢 {ann['title']}",
            "description": ann.get("content", ""),
            "color":       0xC9A84C,
            "footer":      {"text": "Lowly People · Mural de Avisos"},
        }
        if send_channel_message("@everyone 📢 Novo aviso da liderança!", embed):
            supa_patch("announcements", {"id": f"eq.{ann['id']}"}, {"discord_sent": True})
            print(f"[discord_notifier] ✅ Aviso enviado: {ann['title']}")
        time.sleep(0.5)

    # Verifica votações com discord_notify=true e discord_sent=false
    votes = supa_get("votes", {
        "discord_notify": "eq.true",
        "discord_sent":   "eq.false",
        "select":         "id,title,type",
        "limit":          "10",
    })

    for vote in votes:
        tipo = {"binary": "Sim/Não", "single": "Múltipla Escolha", "multi": "Múltipla Seleção"}.get(vote.get("type",""), "Votação")
        embed = {
            "title":       f"🗳 Nova Votação: {vote['title']}",
            "description": f"Tipo: {tipo}\n\nAcesse o painel para votar!",
            "color":       0x5dbf6e,
            "footer":      {"text": "Lowly People · Votações"},
        }
        if send_channel_message("@everyone 🗳 Nova votação disponível!", embed):
            supa_patch("votes", {"id": f"eq.{vote['id']}"}, {"discord_sent": True})
            print(f"[discord_notifier] ✅ Votação enviada: {vote['title']}")
        time.sleep(0.5)

    if not announcements and not votes:
        print("[discord_notifier] nada para notificar")

    notify_new_announcements_dm()


if __name__ == "__main__":
    run()
