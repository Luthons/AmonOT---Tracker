"""
death_tracker.py
Detecta mortes novas dos membros da Lowly People e notifica no Discord.
Usa Supabase para persistir o snapshot — sem dependência do git.
"""

import json
import os
import time
import requests
from pywebpush import webpush, WebPushException

# ── Configuração ──────────────────────────────────────────────────────────────
DISCORD_TOKEN      = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID", "")
SUPABASE_URL       = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY       = os.environ.get("SUPABASE_SERVICE_KEY", "")

HEADERS_DS = {
    "Authorization": f"Bot {DISCORD_TOKEN}",
    "Content-Type":  "application/json",
}

SUPA_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}

VAPID_PRIVATE_KEY = """-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgPiCH0W+G48FvoemQ
4ljD94Oq6039qq/WoDO0yyr8rYuhRANCAAS9ElooYxOvtqR2GlV6yD2t0W6H+C0g
89yKwzfmgRjS+VO+zpQUl3yuoOLfWhC8phukE2Oy64GL3lKQJ549WqhH
-----END PRIVATE KEY-----"""

VAPID_CLAIMS = {"sub": "mailto:lowlypeople@guild.gg"}


# ── Supabase Snapshot ─────────────────────────────────────────────────────────

def load_snapshot() -> set:
    """Carrega IDs de mortes já notificadas do Supabase."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[death_tracker] ⚠ Supabase não configurado, snapshot vazio")
        return set()
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/death_snapshot",
            headers=SUPA_HEADERS,
            params={"select": "id", "limit": "2000", "order": "notified_at.desc"},
            timeout=10,
        )
        if r.status_code != 200:
            print(f"[death_tracker] erro ao carregar snapshot: {r.status_code}")
            return set()
        return {row["id"] for row in r.json()}
    except Exception as e:
        print(f"[death_tracker] erro ao carregar snapshot: {e}")
        return set()


def save_to_snapshot(key: str):
    """Salva um ID de morte no Supabase imediatamente após notificar."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/death_snapshot",
            headers={**SUPA_HEADERS, "Prefer": "resolution=ignore-duplicates,return=minimal"},
            json={"id": key},
            timeout=10,
        )
    except Exception as e:
        print(f"[death_tracker] erro ao salvar snapshot: {e}")


def cleanup_snapshot():
    """Remove entradas antigas (> 7 dias) para não crescer infinitamente."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    try:
        from datetime import datetime, timedelta
        cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
        requests.delete(
            f"{SUPABASE_URL}/rest/v1/death_snapshot",
            headers=SUPA_HEADERS,
            params={"notified_at": f"lt.{cutoff}"},
            timeout=10,
        )
    except Exception as e:
        print(f"[death_tracker] erro ao limpar snapshot: {e}")


def event_key(death: dict) -> str:
    return f"{death['player']}|{death['time']}"


# ── Discord ───────────────────────────────────────────────────────────────────

def send_channel_message(embed: dict, mention_everyone: bool = False) -> bool:
    if not DISCORD_CHANNEL_ID:
        print("[death_tracker] DISCORD_CHANNEL_ID não configurado")
        return False

    payload = {"embeds": [embed]}
    if mention_everyone:
        payload["content"] = "@everyone"

    for attempt in range(3):
        r = requests.post(
            f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages",
            headers=HEADERS_DS,
            json=payload,
            timeout=10,
        )
        if r.status_code in (200, 201):
            return True
        if r.status_code == 429:
            retry_after = r.json().get("retry_after", 1)
            print(f"[death_tracker] rate limit — aguardando {retry_after}s")
            time.sleep(retry_after + 0.1)
        else:
            print(f"[death_tracker] erro canal: {r.status_code} {r.text[:200]}")
            return False
    return False


def get_dm_channel(user_id: str) -> str | None:
    r = requests.post(
        "https://discord.com/api/v10/users/@me/channels",
        headers=HEADERS_DS,
        json={"recipient_id": user_id},
        timeout=10,
    )
    if r.status_code in (200, 201):
        return r.json()["id"]
    print(f"[death_tracker] erro ao abrir DM: {r.status_code}")
    return None


def send_dm(user_id: str, embed: dict) -> bool:
    channel_id = get_dm_channel(user_id)
    if not channel_id:
        return False

    for attempt in range(3):
        r = requests.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers=HEADERS_DS,
            json={"embeds": [embed]},
            timeout=10,
        )
        if r.status_code in (200, 201):
            return True
        if r.status_code == 429:
            retry_after = r.json().get("retry_after", 1)
            time.sleep(retry_after + 0.1)
        else:
            print(f"[death_tracker] erro DM: {r.status_code} {r.text[:200]}")
            return False
    return False


# ── Push Notifications ───────────────────────────────────────────────────────

def get_push_subscriptions(profile_id: str) -> list:
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/push_subscriptions",
            headers=SUPA_HEADERS,
            params={"profile_id": f"eq.{profile_id}", "select": "subscription"},
            timeout=10,
        )
        if r.status_code == 200:
            return [row["subscription"] for row in r.json()]
        return []
    except Exception as e:
        print(f"[death_tracker] erro ao buscar push subs: {e}")
        return []


def send_push_notification(subscription: dict, death: dict, is_enemy: bool) -> bool:
    if not VAPID_PRIVATE_KEY:
        return False
    try:
        import json as _json
        payload = _json.dumps({
            "title": "💀 Você foi morto!",
            "body":  f"Morto por {death['killedBy']} às {death['time']}",
            "tag":   f"death-{death['player']}-{death['time']}",
            "url":   "https://amon-ot-tracker.vercel.app",
            "icon":  "https://amon-ot-tracker.vercel.app/icon-192.png",
        })
        webpush(
            subscription_info=subscription,
            data=payload,
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS,
        )
        return True
    except WebPushException as e:
        print(f"[death_tracker] push error: {e}")
        return False
    except Exception as e:
        print(f"[death_tracker] push error: {e}")
        return False


# ── Embeds ────────────────────────────────────────────────────────────────────

def build_embed(death: dict, is_enemy: bool) -> dict:
    color      = 0xC95050 if is_enemy else 0x5A5040
    title      = "⚔️ Morte PvP — Inimigo" if is_enemy else "💀 Morte PvP"
    killer_url = f"https://amonot.online/characters?name={requests.utils.quote(death['killedBy'])}"
    player_url = f"https://amonot.online/characters?name={requests.utils.quote(death['player'])}"

    return {
        "title": title,
        "color": color,
        "fields": [
            {"name": "Aliado",    "value": f"[{death['player']}]({player_url})",   "inline": True},
            {"name": "Morto por", "value": f"[{death['killedBy']}]({killer_url})", "inline": True},
            {"name": "Horário",   "value": death["time"],                           "inline": False},
        ],
        "footer": {"text": "Lowly People · Death Tracker"},
    }


def build_embed_dm(death: dict, is_enemy: bool) -> dict:
    color      = 0xC95050 if is_enemy else 0x5A5040
    killer_url = f"https://amonot.online/characters?name={requests.utils.quote(death['killedBy'])}"
    alert      = "⚠️ Você foi morto por um **inimigo conhecido**!" if is_enemy else "Você foi morto em PvP."

    return {
        "title":       f"💀 {death['player']} foi morto!",
        "description": alert,
        "color":       color,
        "fields": [
            {"name": "Morto por", "value": f"[{death['killedBy']}]({killer_url})", "inline": True},
            {"name": "Horário",   "value": death["time"],                           "inline": True},
        ],
        "footer": {"text": "Lowly People · Death Tracker — Para silenciar notificações, acesse seu perfil no site."},
    }


# ── Supabase lookup ───────────────────────────────────────────────────────────

def get_profile_id_for_char(char_name: str) -> str | None:
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/characters",
            headers=SUPA_HEADERS,
            params={"name": f"ilike.{char_name}", "select": "profile_id"},
            timeout=10,
        )
        if r.status_code == 200 and r.json():
            return r.json()[0].get("profile_id")
        return None
    except Exception as e:
        print(f"[death_tracker] erro ao buscar profile_id: {e}")
        return None


def get_discord_id_for_char(char_name: str) -> tuple[str | None, bool]:
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/characters",
            headers=SUPA_HEADERS,
            params={"name": f"ilike.{char_name}", "select": "profile_id"},
            timeout=10,
        )
        if r.status_code != 200 or not r.json():
            return None, True

        profile_id = r.json()[0]["profile_id"]

        r2 = requests.get(
            f"{SUPABASE_URL}/rest/v1/profiles",
            headers=SUPA_HEADERS,
            params={"id": f"eq.{profile_id}", "select": "discord_id,death_dm"},
            timeout=10,
        )
        if r2.status_code != 200 or not r2.json():
            return None, True

        profile  = r2.json()[0]
        death_dm = profile.get("death_dm", True)
        if death_dm is None:
            death_dm = True

        return profile.get("discord_id"), death_dm

    except Exception as e:
        print(f"[death_tracker] erro ao buscar Discord ID: {e}")
        return None, True


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    if not DISCORD_TOKEN:
        print("[death_tracker] ⚠ DISCORD_BOT_TOKEN não configurado")
        return

    try:
        with open("guild_data.json", encoding="utf-8") as f:
            guild_data = json.load(f)
    except Exception as e:
        print(f"[death_tracker] erro ao ler guild_data.json: {e}")
        return

    deaths_global = guild_data.get("deaths_global", [])
    if not deaths_global:
        print("[death_tracker] nenhuma morte global encontrada")
        return

    enemy_set = set()
    for eg in guild_data.get("enemy_guilds", []):
        for m in eg.get("members", []):
            enemy_set.add(m["name"].lower())

    notified  = load_snapshot()
    new_count = 0

    for death in deaths_global:
        key = event_key(death)
        if key in notified:
            continue

        is_enemy = death["killedBy"].lower() in enemy_set
        embed    = build_embed(death, is_enemy)

        canal_ok = send_channel_message(embed, mention_everyone=True)
        if canal_ok:
            print(f"[death_tracker] ✅ canal: {death['player']} morto por {death['killedBy']}")
            new_count += 1
            # Salva imediatamente no Supabase — antes de qualquer outro run
            save_to_snapshot(key)
            notified.add(key)
        else:
            print(f"[death_tracker] ❌ falha canal: {death['player']}")

        if SUPABASE_URL and SUPABASE_KEY:
            discord_id, death_dm_enabled = get_discord_id_for_char(death["player"])
            if discord_id and death_dm_enabled:
                embed_dm = build_embed_dm(death, is_enemy)
                if send_dm(discord_id, embed_dm):
                    print(f"[death_tracker] ✅ DM: {death['player']} → {discord_id}")
                else:
                    print(f"[death_tracker] ❌ falha DM: {death['player']}")
            elif discord_id and not death_dm_enabled:
                print(f"[death_tracker] ℹ {death['player']} optou por não receber DMs")
            else:
                print(f"[death_tracker] ℹ {death['player']} sem discord_id cadastrado")

            # Push notification
            profile_id = get_profile_id_for_char(death["player"])
            if profile_id:
                subs = get_push_subscriptions(profile_id)
                for sub in subs:
                    if send_push_notification(sub, death, is_enemy):
                        print(f"[death_tracker] ✅ Push: {death['player']}")
                    else:
                        print(f"[death_tracker] ❌ Push falhou: {death['player']}")

        time.sleep(0.5)

    # Limpeza semanal de entradas antigas
    cleanup_snapshot()
    print(f"[death_tracker] ✅ {new_count} novas mortes notificadas")


if __name__ == "__main__":
    run()
