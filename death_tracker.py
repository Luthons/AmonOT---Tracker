"""
death_tracker.py
Detecta mortes novas dos membros da Lowly People e notifica no Discord.
- Canal da guilda: todas as mortes PvP
- DM do player: notifica o dono do personagem (se cadastrado no Supabase)
Roda junto com o scraper via GitHub Actions.
"""

import json
import os
import time
import requests

# ── Configuração ──────────────────────────────────────────────────────────────
DISCORD_TOKEN      = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID", "")
DISCORD_USER_ID    = os.environ.get("DISCORD_USER_ID", "")  # admin fallback

SNAPSHOT_PATH = "death_snapshot.json"

HEADERS_DS = {
    "Authorization": f"Bot {DISCORD_TOKEN}",
    "Content-Type":  "application/json",
}


# ── Snapshot ──────────────────────────────────────────────────────────────────

def load_snapshot() -> set:
    """Carrega o conjunto de chaves de mortes já notificadas."""
    try:
        with open(SNAPSHOT_PATH, encoding="utf-8") as f:
            data = json.load(f)
            return set(data.get("notified_keys", []))
    except (FileNotFoundError, json.JSONDecodeError):
        print("[death_tracker] nenhum snapshot anterior, iniciando do zero")
        return set()


def save_snapshot(keys: set):
    # Mantém apenas as últimas 2000 chaves para não crescer infinitamente
    keys_list = list(keys)[-2000:]
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump({"notified_keys": keys_list}, f, ensure_ascii=False)


def event_key(death: dict) -> str:
    return f"{death['player']}|{death['time']}"


# ── Discord ───────────────────────────────────────────────────────────────────

def send_channel_message(embed: dict, mention_everyone: bool = False) -> bool:
    """Envia mensagem embed no canal da guilda."""
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
    """Abre ou obtém o canal de DM com o usuário."""
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
    """Envia DM para um usuário do Discord."""
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


# ── Embeds ────────────────────────────────────────────────────────────────────

def build_embed(death: dict, is_enemy: bool) -> dict:
    """Monta o embed de notificação de morte."""
    color   = 0xC95050 if is_enemy else 0x5A5040
    title   = "⚔️ Morte PvP — Inimigo" if is_enemy else "💀 Morte PvP"
    killer_url = f"https://amonot.online/characters?name={requests.utils.quote(death['killedBy'])}"
    player_url = f"https://amonot.online/characters?name={requests.utils.quote(death['player'])}"

    return {
        "title":       title,
        "color":       color,
        "fields": [
            {"name": "Aliado",   "value": f"[{death['player']}]({player_url})",    "inline": True},
            {"name": "Morto por","value": f"[{death['killedBy']}]({killer_url})",  "inline": True},
            {"name": "Horário",  "value": death["time"],                            "inline": False},
        ],
        "footer": {"text": "Lowly People · Death Tracker"},
    }


# ── Supabase lookup ───────────────────────────────────────────────────────────

def get_discord_id_for_char(char_name: str, supabase_url: str, supabase_key: str) -> str | None:
    """Busca o Discord ID do dono de um personagem no Supabase."""
    try:
        # Busca o personagem
        r = requests.get(
            f"{supabase_url}/rest/v1/characters",
            headers={
                "apikey":        supabase_key,
                "Authorization": f"Bearer {supabase_key}",
            },
            params={"name": f"ilike.{char_name}", "select": "profile_id"},
            timeout=10,
        )
        if r.status_code != 200 or not r.json():
            return None

        profile_id = r.json()[0]["profile_id"]

        # Busca o perfil
        r2 = requests.get(
            f"{supabase_url}/rest/v1/profiles",
            headers={
                "apikey":        supabase_key,
                "Authorization": f"Bearer {supabase_key}",
            },
            params={"id": f"eq.{profile_id}", "select": "discord_id"},
            timeout=10,
        )
        if r2.status_code != 200 or not r2.json():
            return None

        return r2.json()[0].get("discord_id")

    except Exception as e:
        print(f"[death_tracker] erro ao buscar Discord ID: {e}")
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    if not DISCORD_TOKEN:
        print("[death_tracker] ⚠ DISCORD_BOT_TOKEN não configurado")
        return

    # Carrega guild_data.json
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

    # Monta set de inimigos
    enemy_set = set()
    for eg in guild_data.get("enemy_guilds", []):
        for m in eg.get("members", []):
            enemy_set.add(m["name"].lower())

    notified = load_snapshot()
    new_count = 0

    for death in deaths_global:
        key = event_key(death)
        if key in notified:
            continue

        is_enemy = death["killedBy"].lower() in enemy_set
        embed    = build_embed(death, is_enemy)

        # Envia no canal da guilda
        if send_channel_message(embed, mention_everyone=True):
            print(f"[death_tracker] ✅ canal: {death['player']} morto por {death['killedBy']}")
            new_count += 1
        else:
            print(f"[death_tracker] ❌ falha ao enviar: {death['player']}")

        notified.add(key)
        time.sleep(0.5)  # evita rate limit

    save_snapshot(notified)
    print(f"[death_tracker] ✅ {new_count} novas mortes notificadas")


if __name__ == "__main__":
    run()
