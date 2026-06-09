"""
telegram_bot.py
Execução única (cron job): processa updates pendentes do Telegram e encerra.
O offset é persistido no Supabase (tabela bot_config) para não reprocessar.
"""

import requests
import os
import json

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
SUPABASE_URL   = os.environ.get("SUPABASE_URL")
SUPABASE_KEY   = os.environ.get("SUPABASE_SERVICE_KEY")

SUPA_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}


def get_offset():
    """Busca o último offset processado do Supabase."""
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/bot_config?key=eq.telegram_offset&select=value",
            headers=SUPA_HEADERS,
            timeout=10,
        )
        data = r.json()
        return int(data[0]["value"]) if data else 0
    except Exception:
        return 0


def save_offset(offset):
    """Salva o offset no Supabase."""
    requests.post(
        f"{SUPABASE_URL}/rest/v1/bot_config",
        headers={**SUPA_HEADERS, "Prefer": "resolution=merge-duplicates"},
        json={"key": "telegram_offset", "value": str(offset)},
        timeout=10,
    )


def get_updates(offset):
    r = requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
        params={"offset": offset, "timeout": 0, "limit": 100},
        timeout=15,
    )
    return r.json().get("result", [])


def register_telegram_id(telegram_id, username):
    username_clean = username.lstrip("@").lower()
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/profiles?select=id&or=(telegram_username.ilike.{username_clean},telegram_username.ilike.@{username_clean})",
        headers=SUPA_HEADERS,
        timeout=10,
    )
    profiles = r.json()
    if not profiles or not isinstance(profiles, list) or not profiles:
        return False
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{profiles[0]['id']}",
        headers={**SUPA_HEADERS, "Prefer": "return=minimal"},
        json={"telegram_id": telegram_id},
        timeout=10,
    )
    return True


def send_message(chat_id, text):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=10,
    )


def run():
    if not TELEGRAM_TOKEN or not SUPABASE_URL or not SUPABASE_KEY:
        print("[telegram_bot] configuracao incompleta")
        return

    offset  = get_offset()
    updates = get_updates(offset)

    for update in updates:
        offset = update["update_id"] + 1
        msg    = update.get("message", {})
        text   = msg.get("text", "")
        user   = msg.get("from", {})

        if not text.startswith("/start"):
            continue

        telegram_id = user["id"]
        username    = user.get("username", "")

        if not username:
            send_message(telegram_id, "❌ Seu perfil do Telegram não tem username configurado. Configure em Configurações > Nome de usuário e tente novamente.")
        elif register_telegram_id(telegram_id, username):
            send_message(telegram_id, "✅ Telegram vinculado com sucesso ao seu perfil da guilda!")
            print(f"[telegram_bot] vinculado: @{username} -> {telegram_id}")
        else:
            send_message(telegram_id, "❌ Username não encontrado. Cadastre seu @username do Telegram no perfil do site primeiro.")
            print(f"[telegram_bot] username nao encontrado: @{username}")

    save_offset(offset)
    print(f"[telegram_bot] {len(updates)} updates processados")


if __name__ == "__main__":
    run()
