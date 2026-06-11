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
from activity_log import log as alog

# ── Configuração ──────────────────────────────────────────────────────────────
DISCORD_TOKEN      = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID", "")
SUPABASE_URL       = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY       = os.environ.get("SUPABASE_SERVICE_KEY", "")
TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN")

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


def save_to_snapshot(key: str, victim: str = None, killer: str = None):
    """Salva um ID de morte no Supabase imediatamente após notificar."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    try:
        payload = {"id": key}
        if victim: payload["victim"] = victim
        if killer: payload["killer"] = killer
        requests.post(
            f"{SUPABASE_URL}/rest/v1/death_snapshot",
            headers={**SUPA_HEADERS, "Prefer": "resolution=ignore-duplicates,return=minimal"},
            json=payload,
            timeout=10,
        )
    except Exception as e:
        print(f"[death_tracker] erro ao salvar snapshot: {e}")




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


def send_telegram_dm(telegram_id: int, message: str) -> bool:
    if not TELEGRAM_TOKEN or not telegram_id:
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": telegram_id, "text": message, "parse_mode": "HTML"},
        timeout=10,
    )
    return r.status_code == 200


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


def get_discord_id_for_char(char_name: str) -> tuple[str | None, int | None, bool]:
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/characters",
            headers=SUPA_HEADERS,
            params={"name": f"ilike.{char_name}", "select": "profile_id"},
            timeout=10,
        )
        if r.status_code != 200 or not r.json():
            return None, None, True

        profile_id = r.json()[0]["profile_id"]

        r2 = requests.get(
            f"{SUPABASE_URL}/rest/v1/profiles",
            headers=SUPA_HEADERS,
            params={"id": f"eq.{profile_id}", "select": "discord_id,telegram_id,death_dm"},
            timeout=10,
        )
        if r2.status_code != 200 or not r2.json():
            return None, None, True

        profile     = r2.json()[0]
        death_dm    = profile.get("death_dm", True)
        if death_dm is None:
            death_dm = True

        return profile.get("discord_id"), profile.get("telegram_id"), death_dm

    except Exception as e:
        print(f"[death_tracker] erro ao buscar Discord ID: {e}")
        return None, None, True


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
            save_to_snapshot(key, victim=death.get("player"), killer=death.get("killedBy"))
            notified.add(key)
        else:
            print(f"[death_tracker] ❌ falha canal: {death['player']}")

        if SUPABASE_URL and SUPABASE_KEY:
            discord_id, telegram_id, death_dm_enabled = get_discord_id_for_char(death["player"])
            if death_dm_enabled:
                embed_dm = build_embed_dm(death, is_enemy)
                if discord_id:
                    if send_dm(discord_id, embed_dm):
                        print(f"[death_tracker] ✅ DM Discord: {death['player']} → {discord_id}")
                    else:
                        print(f"[death_tracker] ❌ falha DM Discord: {death['player']}")
                else:
                    print(f"[death_tracker] ℹ {death['player']} sem discord_id cadastrado")
                if telegram_id:
                    victim = death["player"]
                    killer = death["killedBy"]
                    msg = f"💀 <b>{victim}</b> foi morto por <b>{killer}</b>"
                    if send_telegram_dm(telegram_id, msg):
                        print(f"[death_tracker] ✅ DM Telegram: {death['player']}")
                    else:
                        print(f"[death_tracker] ❌ falha DM Telegram: {death['player']}")
            else:
                print(f"[death_tracker] ℹ {death['player']} optou por não receber DMs")

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

    print(f"[death_tracker] ✅ {new_count} novas mortes notificadas")

    # Verifica hunted list automática
    check_auto_hunted(guild_data)


def check_auto_hunted(guild_data: dict):
    """Verifica jogadores externos com 2+ kills em membros LP e adiciona à hunted list."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return

    # Monta set de membros LP
    lp_members = {m["name"].lower() for m in guild_data.get("members", [])}

    # Conta kills por killer externo
    kill_counts = {}
    for death in guild_data.get("deaths_global", []):
        if not death.get("isPvp"):
            continue
        player  = death.get("player", "").lower()
        killer  = death.get("killedBy", "")
        if not player or not killer:
            continue
        # Só conta se vítima é LP e killer não é LP
        if player not in lp_members:
            continue
        if killer.lower() in lp_members:
            continue
        kill_counts[killer] = kill_counts.get(killer, 0) + 1

    # Busca hunted list atual
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/hunted_list",
            headers=SUPA_HEADERS,
            params={"select": "name"},
            timeout=10,
        )
        existing = {row["name"].lower() for row in (r.json() if r.status_code == 200 else [])}
    except:
        existing = set()

    # Adiciona quem tem 2+ kills e não está na lista
    for killer, count in kill_counts.items():
        if count >= 2 and killer.lower() not in existing:
            try:
                requests.post(
                    f"{SUPABASE_URL}/rest/v1/hunted_list",
                    headers={**SUPA_HEADERS, "Prefer": "resolution=ignore-duplicates,return=minimal"},
                    json={
                        "name":     killer,
                        "reason":   f"Matou {count} membro(s) LP (auto-detectado)",
                        "added_by": "Sistema",
                        "source":   "auto",
                    },
                    timeout=10,
                )
                print(f"[death_tracker] ⚠ {killer} adicionado à hunted list ({count} kills)")
                alog(
                    actor    = "Sistema",
                    action   = f"{killer} adicionado à Hunted List automaticamente ({count} kills em membros LP)",
                    category = "hunted",
                    details  = {"player": killer, "kill_count": count, "source": "auto"},
                )
            except Exception as e:
                print(f"[death_tracker] erro ao adicionar hunted: {e}")


if __name__ == "__main__":
    run()
