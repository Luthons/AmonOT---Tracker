"""
event_reminder.py
Envia DM no Discord para membros que confirmaram presença em eventos
que começam em aproximadamente 1 hora.
Roda via GitHub Actions a cada 1 minuto.
"""

import os
import time
import requests
from datetime import datetime, timedelta, timezone

# ── Configuração ──────────────────────────────────────────────────────────────
DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
SUPABASE_URL  = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY", "")

# Janela de lembrete: eventos que começam entre 55 e 65 minutos a partir de agora
REMINDER_MIN = 55
REMINDER_MAX = 65

SUPA_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}

DISCORD_HEADERS = {
    "Authorization": f"Bot {DISCORD_TOKEN}",
    "Content-Type":  "application/json",
}

EVENT_EMOJIS = {
    "quest":     "🗺️",
    "expedicao": "⚓",
    "vip":       "⭐",
    "task":      "📋",
    "boss":      "💀",
    "hunt":      "🗡️",
}


# ── Supabase ──────────────────────────────────────────────────────────────────

def supa_get(table: str, params: dict) -> list:
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=SUPA_HEADERS,
            params=params,
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
        print(f"[reminder] erro Supabase GET {table}: {r.status_code} {r.text[:100]}")
        return []
    except Exception as e:
        print(f"[reminder] erro Supabase: {e}")
        return []


def get_upcoming_events() -> list:
    """Busca eventos que começam entre 55 e 65 minutos a partir de agora (horário de Brasília = UTC-3)."""
    now_utc   = datetime.now(timezone.utc)
    # Converte para Brasília
    now_brt   = now_utc - timedelta(hours=3)
    win_start = now_brt + timedelta(minutes=REMINDER_MIN)
    win_end   = now_brt + timedelta(minutes=REMINDER_MAX)

    # Busca eventos do dia atual e seguinte para cobrir virada de dia
    dates = set([win_start.strftime("%Y-%m-%d"), win_end.strftime("%Y-%m-%d")])
    events = []
    for date in dates:
        rows = supa_get("calendar_events", {"data": f"eq.{date}", "select": "*"})
        events.extend(rows)

    # Filtra pela janela de horário
    upcoming = []
    for ev in events:
        try:
            hora = ev.get("hora", "")[:5]  # "HH:MM"
            ev_dt = datetime.strptime(f"{ev['data']} {hora}", "%Y-%m-%d %H:%M")
            if win_start.replace(tzinfo=None) <= ev_dt <= win_end.replace(tzinfo=None):
                upcoming.append(ev)
        except Exception:
            continue

    return upcoming


def get_joins_for_event(event_id: str) -> list:
    """Retorna lista de joins com profile_id para um evento."""
    return supa_get("calendar_joins", {
        "evento_id": f"eq.{event_id}",
        "select":    "player_name,profile_id",
    })


def get_discord_id(profile_id: str) -> str | None:
    """Busca discord_id de um perfil no Supabase."""
    rows = supa_get("profiles", {"id": f"eq.{profile_id}", "select": "discord_id"})
    if rows and rows[0].get("discord_id"):
        return rows[0]["discord_id"]
    return None


def get_discord_id_by_char(player_name: str) -> str | None:
    """Busca discord_id pelo nome do personagem (fallback quando profile_id é null)."""
    chars = supa_get("characters", {"name": f"ilike.{player_name}", "select": "profile_id"})
    if not chars:
        return None
    profile_id = chars[0].get("profile_id")
    if not profile_id:
        return None
    return get_discord_id(profile_id)


# ── Discord ───────────────────────────────────────────────────────────────────

def get_dm_channel(user_id: str) -> str | None:
    r = requests.post(
        "https://discord.com/api/v10/users/@me/channels",
        headers=DISCORD_HEADERS,
        json={"recipient_id": user_id},
        timeout=10,
    )
    if r.status_code in (200, 201):
        return r.json()["id"]
    print(f"[reminder] erro ao abrir DM: {r.status_code}")
    return None


def send_dm(user_id: str, embed: dict) -> bool:
    channel_id = get_dm_channel(user_id)
    if not channel_id:
        return False

    for attempt in range(3):
        r = requests.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers=DISCORD_HEADERS,
            json={"embeds": [embed]},
            timeout=10,
        )
        if r.status_code in (200, 201):
            return True
        if r.status_code == 429:
            retry_after = r.json().get("retry_after", 1)
            time.sleep(retry_after + 0.1)
        else:
            print(f"[reminder] erro DM: {r.status_code} {r.text[:100]}")
            return False
    return False


def build_embed(event: dict) -> dict:
    tipo  = (event.get("tipo") or "").lower()
    emoji = EVENT_EMOJIS.get(tipo, "📅")
    hora  = (event.get("hora") or "")[:5]
    share = event.get("share")

    fields = [
        {"name": "⏰ Horário", "value": hora, "inline": True},
        {"name": "📋 Tipo",   "value": tipo.capitalize(), "inline": True},
    ]

    if event.get("vagas"):
        fields.append({"name": "👥 Vagas", "value": str(event["vagas"]), "inline": True})

    if share and tipo in ("boss", "hunt"):
        fields.append({"name": "⚔ Share", "value": f"Min: {share['min']} | Max: {share['max']}", "inline": True})

    if event.get("descricao"):
        fields.append({"name": "📝 Descrição", "value": event["descricao"], "inline": False})

    return {
        "title":       f"{emoji} Lembrete de Evento — {event['nome']}",
        "description": "Seu evento começa em **aproximadamente 1 hora**!",
        "color":       0xC9A84C,
        "fields":      fields,
        "footer":      {"text": "Lowly People · Calendário da Guilda"},
        "timestamp":   datetime.utcnow().isoformat(),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    if not DISCORD_TOKEN:
        print("[reminder] ⚠ DISCORD_BOT_TOKEN não configurado")
        return
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[reminder] ⚠ Supabase não configurado")
        return

    events = get_upcoming_events()
    if not events:
        print("[reminder] nenhum evento nas próximas ~1h")
        return

    print(f"[reminder] {len(events)} evento(s) para lembrar")

    for event in events:
        joins = get_joins_for_event(event["id"])
        if not joins:
            print(f"[reminder] '{event['nome']}' — sem joins")
            continue

        print(f"[reminder] '{event['nome']}' — {len(joins)} join(s)")
        embed = build_embed(event)

        for join in joins:
            player_name = join.get("player_name", "")
            profile_id  = join.get("profile_id")

            # Tenta pelo profile_id primeiro, senão pelo nome do personagem
            discord_id = None
            if profile_id:
                discord_id = get_discord_id(profile_id)
            if not discord_id:
                discord_id = get_discord_id_by_char(player_name)

            if not discord_id:
                print(f"[reminder] ℹ {player_name} sem discord_id")
                continue

            if send_dm(discord_id, embed):
                print(f"[reminder] ✅ DM enviada: {player_name}")
            else:
                print(f"[reminder] ❌ falha DM: {player_name}")

            time.sleep(0.3)


if __name__ == "__main__":
    run()
