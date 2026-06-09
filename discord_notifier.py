"""
discord_notifier.py
Verifica novos avisos e votações com discord_notify=true
e envia @everyone no canal de anúncios.
Envia notificações em paralelo no Telegram (grupo e DMs individuais).
"""

import os
import time
import requests
from datetime import datetime, timedelta, timezone

DISCORD_TOKEN    = os.environ.get("DISCORD_BOT_TOKEN", "")
ANNOUNCE_CHANNEL = os.environ.get("DISCORD_ANNOUNCE_CHANNEL_ID", "")
SUPABASE_URL     = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY     = os.environ.get("SUPABASE_SERVICE_KEY", "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_GROUP_ID = os.environ.get("TELEGRAM_GROUP_ID")

SUPA_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}

DISCORD_HEADERS = {
    "Authorization": f"Bot {DISCORD_TOKEN}",
    "Content-Type":  "application/json",
}


# ── Supabase ──────────────────────────────────────────────────────────────────

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


# ── Discord ───────────────────────────────────────────────────────────────────

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


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram_group(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_GROUP_ID:
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_GROUP_ID, "text": message, "parse_mode": "HTML"},
        timeout=10,
    )
    return r.status_code == 200


def send_telegram_dm(telegram_id: int, message: str):
    if not TELEGRAM_TOKEN:
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": telegram_id, "text": message, "parse_mode": "HTML"},
        timeout=10,
    )
    return r.status_code == 200


def get_telegram_id_from_username(username: str) -> int | None:
    """Busca telegram_id do Supabase pelo username — populado via /start do bot."""
    pass  # será preenchido via /start


def notify_telegram_all(message: str):
    """Envia DM no Telegram para todos os usuários com telegram_id cadastrado."""
    if not TELEGRAM_TOKEN:
        return
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/profiles?select=telegram_id&telegram_id=not.is.null",
            headers=SUPA_HEADERS,
            timeout=10,
        )
        profiles = r.json()
        print(f"[telegram] disparando DM para {len(profiles)} usuários")
        for p in profiles:
            tid = p.get("telegram_id")
            if tid:
                send_telegram_dm(tid, message)
                time.sleep(0.1)
    except Exception as e:
        print(f"[telegram] erro notify_all: {e}")


# ── Notificação por preferência de jogo ───────────────────────────────────────

def notify_new_announcements_dm():
    """
    Busca avisos criados nas últimas 2 horas com notify_groups não vazio
    e envia DM individual para membros com as preferências correspondentes.
    Envia via Discord e Telegram em paralelo.
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
        "select": "id,discord_id,telegram_id,game_preferences,display_name",
        "limit":  "500",
    })

    for ann in announcements:
        notify_groups = ann.get("notify_groups", [])
        ann_id = ann["id"]

        targets = [
            p for p in profiles
            if (p.get("discord_id") or p.get("telegram_id"))
            and any(g in (p.get("game_preferences") or []) for g in notify_groups)
        ]

        print(f"[discord_notifier] aviso '{ann['title']}' → {len(targets)} membro(s) a notificar por DM")

        discord_embed = {
            "title":       f"📢 {ann['title']}",
            "description": ann.get("content", ""),
            "color":       0xC9A84C,
            "footer":      {"text": "Lowly People · Mural de Avisos"},
        }
        telegram_text = f"📢 <b>{ann['title']}</b>\n\n{ann.get('content', '')}"

        for profile in targets:
            pid        = profile["id"]
            discord_id = profile.get("discord_id")
            telegram_id = profile.get("telegram_id")

            sent = supa_get("announcement_notify_sent", {
                "announcement_id": f"eq.{ann_id}",
                "profile_id":      f"eq.{pid}",
                "select":          "announcement_id",
            })
            if sent:
                continue

            notified = False
            name = profile.get("display_name", "?")

            if discord_id and send_dm(discord_id, discord_embed):
                notified = True
                print(f"[discord_notifier] ✅ DM Discord aviso → {name}")

            if telegram_id and send_telegram_dm(telegram_id, telegram_text):
                notified = True
                print(f"[discord_notifier] ✅ DM Telegram aviso → {name}")

            if notified:
                try:
                    requests.post(
                        f"{SUPABASE_URL}/rest/v1/announcement_notify_sent",
                        headers={**SUPA_HEADERS, "Prefer": "resolution=ignore-duplicates,return=minimal"},
                        json={"announcement_id": ann_id, "profile_id": pid},
                        timeout=10,
                    )
                except Exception as e:
                    print(f"[discord_notifier] erro mark sent: {e}")

            time.sleep(0.3)


# ── Main ──────────────────────────────────────────────────────────────────────

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
        discord_ok = send_channel_message("@everyone 📢 Novo aviso da liderança!", embed)
        telegram_ok = send_telegram_group(f"📢 <b>{ann['title']}</b>\n\n{ann.get('content', '')}")
        if discord_ok or telegram_ok:
            supa_patch("announcements", {"id": f"eq.{ann['id']}"}, {"discord_sent": True})
            print(f"[discord_notifier] ✅ Aviso enviado: {ann['title']}")
            notify_telegram_all(f"📢 <b>Aviso da Guilda — Lowly People</b>\n\n{ann.get('content', '')}")
        time.sleep(0.5)

    # Verifica votações com discord_notify=true e discord_sent=false
    votes = supa_get("votes", {
        "discord_notify": "eq.true",
        "discord_sent":   "eq.false",
        "select":         "id,title,type",
        "limit":          "10",
    })

    for vote in votes:
        tipo = {"binary": "Sim/Não", "single": "Múltipla Escolha", "multi": "Múltipla Seleção"}.get(vote.get("type", ""), "Votação")
        embed = {
            "title":       f"🗳 Nova Votação: {vote['title']}",
            "description": f"Tipo: {tipo}\n\nAcesse o painel para votar!",
            "color":       0x5dbf6e,
            "footer":      {"text": "Lowly People · Votações"},
        }
        discord_ok = send_channel_message("@everyone 🗳 Nova votação disponível!", embed)
        telegram_ok = send_telegram_group(f"🗳 <b>Nova Votação: {vote['title']}</b>\n\nTipo: {tipo}\n\nAcesse o painel para votar!")
        if discord_ok or telegram_ok:
            supa_patch("votes", {"id": f"eq.{vote['id']}"}, {"discord_sent": True})
            print(f"[discord_notifier] ✅ Votação enviada: {vote['title']}")
        time.sleep(0.5)

    if not announcements and not votes:
        print("[discord_notifier] nada para notificar")

    notify_new_announcements_dm()


if __name__ == "__main__":
    run()
