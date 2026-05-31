"""
discord_notifier.py
Verifica novos avisos e votações com discord_notify=true
e envia @everyone no canal de anúncios.
"""

import os
import time
import requests

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


if __name__ == "__main__":
    run()
