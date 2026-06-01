"""
kill_notifier.py
Envia @everyone no canal de kills do Discord a cada nova kill processada.
Roda junto com o update_realtime (a cada 1 min).

Controle de duplicatas: tabela kill_rankings tem campo notified (BOOLEAN DEFAULT false).
Este script busca kills com notified=false, envia, marca como notified=true.
"""

import os
import time
import requests
from datetime import datetime, timezone, timedelta

DISCORD_TOKEN    = os.environ.get("DISCORD_BOT_TOKEN", "")
KILLS_CHANNEL_ID = os.environ.get("DISCORD_KILLS_CHANNEL_ID", "")
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

BRASILIA = timezone(timedelta(hours=-3))


def supa_get(table: str, params: dict) -> list:
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=SUPA_HEADERS,
            params=params,
            timeout=10,
        )
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        print(f"[kill_notifier] erro GET: {e}")
        return []


def supa_patch(table: str, params: dict, body: dict):
    try:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers={**SUPA_HEADERS, "Prefer": "return=minimal"},
            params=params,
            json=body,
            timeout=10,
        )
    except Exception as e:
        print(f"[kill_notifier] erro PATCH: {e}")


def send_message(channel_id: str, content: str, embed: dict) -> bool:
    for _ in range(3):
        r = requests.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers=DISCORD_HEADERS,
            json={"content": content, "embeds": [embed]},
            timeout=10,
        )
        if r.status_code in (200, 201):
            return True
        if r.status_code == 429:
            time.sleep(r.json().get("retry_after", 1) + 0.1)
        else:
            print(f"[kill_notifier] Discord erro {r.status_code}: {r.text[:200]}")
            break
    return False


def format_kill_time(kill_time_iso: str) -> str:
    try:
        dt = datetime.fromisoformat(kill_time_iso)
        dt = dt.astimezone(BRASILIA)
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return kill_time_iso


def build_embed(kill: dict) -> dict:
    killer        = kill["killer"]
    victim        = kill["victim"]
    killer_resets = kill.get("killer_resets", 0)
    victim_resets = kill.get("victim_resets", 0)
    points        = kill.get("points", 0)
    is_assist     = kill.get("is_assist", False)
    kill_time     = format_kill_time(kill.get("kill_time", ""))

    if is_assist:
        title = f"🤝 {killer} deu assist na morte de {victim}"
        color = 0x5ab0e8
        pts_label = "Assist"
    else:
        title = f"⚔️ {killer} matou {victim}"
        color = 0xe84040

        # Cor muda conforme pontos
        if points >= 50:
            color = 0xe8a030  # Legendary
        elif points >= 20:
            color = 0x8855cc  # Epic
        elif points >= 10:
            color = 0x5ab0e8  # Rare
        pts_label = "Pontos"

    fields = [
        {"name": "🔁 Killer",  "value": f"{killer} ({killer_resets}rr)", "inline": True},
        {"name": "💀 Vítima",  "value": f"{victim} ({victim_resets}rr)", "inline": True},
        {"name": f"⭐ {pts_label}", "value": str(points),                "inline": True},
        {"name": "🕐 Horário", "value": kill_time,                       "inline": True},
    ]

    return {
        "title":  title,
        "color":  color,
        "fields": fields,
        "footer": {"text": "Lowly People · Kill Tracker"},
    }


def run():
    if not DISCORD_TOKEN:
        print("[kill_notifier] ⚠ DISCORD_BOT_TOKEN não configurado")
        return
    if not KILLS_CHANNEL_ID:
        print("[kill_notifier] ⚠ DISCORD_KILLS_CHANNEL_ID não configurado")
        return
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[kill_notifier] ⚠ Supabase não configurado")
        return

    # Busca kills ainda não notificadas, ordenadas por kill_time
    kills = supa_get("kill_rankings", {
        "notified": "eq.false",
        "order":    "kill_time.asc",
        "select":   "*",
        "limit":    "50",  # máximo 50 por ciclo para não spammar
    })

    if not kills:
        print("[kill_notifier] ✓ nenhuma kill nova para notificar")
        return

    print(f"[kill_notifier] {len(kills)} kills para notificar...")

    for kill in kills:
        embed   = build_embed(kill)
        ok = send_message(KILLS_CHANNEL_ID, "", embed)
        if ok:
            print(f"[kill_notifier] ✅ {kill['killer']} → {kill['victim']} ({kill.get('points')} pts)")
        else:
            print(f"[kill_notifier] ❌ falha ao notificar {kill['killer']} → {kill['victim']}")

        # Marca como notificado independente do resultado
        supa_patch("kill_rankings", {"id": f"eq.{kill['id']}"}, {"notified": True})
        time.sleep(0.5)  # respeita rate limit do Discord

    print(f"[kill_notifier] ✅ {len(kills)} kills processadas")


if __name__ == "__main__":
    run()
