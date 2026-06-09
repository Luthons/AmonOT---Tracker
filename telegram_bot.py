"""
telegram_bot.py
Long-polling bot que captura /start e registra o telegram_id
do usuário no Supabase, vinculando pelo telegram_username do perfil.
"""

import requests
import os
import time

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
SUPABASE_URL   = os.environ.get("SUPABASE_URL")
SUPABASE_KEY   = os.environ.get("SUPABASE_SERVICE_KEY")

SUPA_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}


def get_updates(offset=None):
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    r = requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
        params=params,
        timeout=35,
    )
    return r.json().get("result", [])


def register_telegram_id(telegram_id: int, username: str):
    # Busca perfil pelo telegram_username (com ou sem @)
    clean = username.lstrip("@")
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/profiles",
        headers=SUPA_HEADERS,
        params={"telegram_username": f"ilike.%{clean}%", "select": "id"},
        timeout=10,
    )
    profiles = r.json() if r.status_code == 200 else []
    if not profiles:
        return False
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/profiles",
        headers={**SUPA_HEADERS, "Prefer": "return=minimal"},
        params={"id": f"eq.{profiles[0]['id']}"},
        json={"telegram_id": telegram_id},
        timeout=10,
    )
    return True


def send_message(chat_id: int, text: str):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=10,
    )


def run():
    if not TELEGRAM_TOKEN or not SUPABASE_URL or not SUPABASE_KEY:
        print("[telegram_bot] ⚠ variáveis de ambiente incompletas")
        return

    print("[telegram_bot] iniciando polling...")
    offset = None

    while True:
        try:
            updates = get_updates(offset)
        except Exception as e:
            print(f"[telegram_bot] erro ao buscar updates: {e}")
            time.sleep(5)
            continue

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
                send_message(telegram_id, "❌ Seu usuário do Telegram não tem @username definido. Configure um @username nas configurações do Telegram e tente novamente.")
                continue

            print(f"[telegram_bot] /start de @{username} (id={telegram_id})")

            if register_telegram_id(telegram_id, username):
                send_message(telegram_id, "✅ Telegram vinculado com sucesso ao seu perfil da guilda!")
                print(f"[telegram_bot] ✅ vinculado: @{username} → {telegram_id}")
            else:
                send_message(telegram_id, "❌ Username não encontrado. Cadastre seu @username do Telegram no perfil do site primeiro.")
                print(f"[telegram_bot] ❌ username não encontrado: @{username}")

        time.sleep(1)


if __name__ == "__main__":
    run()
