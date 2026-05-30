"""
kill_ranking.py
Processa kills da guerra e calcula pontuação para o Kill Ranking.
Roda junto com o update_realtime para manter o ranking atualizado.
"""

import json
import os
import requests
from datetime import datetime, timezone, timedelta

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

BRASILIA = timezone(timedelta(hours=-3))

SUPA_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}


# ── Cálculo de pontos ─────────────────────────────────────────────────────────

def calc_points(killer_resets: int, victim_resets: int) -> int:
    """
    Lógica de pontuação:
    - Vítima < 30 resets → 1 ponto base
    - Vítima >= 30 resets → (victim_resets - 30) + 1 pontos base
    - Killer < 30 resets → dobra os pontos
    """
    if victim_resets < 30:
        base = 1
    else:
        base = (victim_resets - 30) + 1

    if killer_resets < 30:
        base *= 2

    return base


# ── Supabase helpers ──────────────────────────────────────────────────────────

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
        return []
    except Exception as e:
        print(f"[kill_ranking] erro Supabase GET: {e}")
        return []


def supa_post(table: str, payload) -> bool:
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers={**SUPA_HEADERS, "Prefer": "resolution=ignore-duplicates,return=minimal"},
            json=payload,
            timeout=10,
        )
        return r.status_code in (200, 201, 204)
    except Exception as e:
        print(f"[kill_ranking] erro Supabase POST: {e}")
        return False


def already_processed(killer: str, victim: str, kill_time: str) -> bool:
    """Verifica se essa kill já foi processada."""
    rows = supa_get("kill_rankings", {
        "killer":    f"eq.{killer}",
        "victim":    f"eq.{victim}",
        "kill_time": f"eq.{kill_time}",
        "select":    "id",
        "limit":     "1",
    })
    return len(rows) > 0


def get_member_resets(name: str, members_map: dict) -> int:
    """Busca resets de um membro da LP pelo nome (case-insensitive)."""
    return members_map.get(name.lower(), 0)


def get_enemy_resets(name: str, enemy_map: dict) -> int:
    """Busca resets de um inimigo pelo nome (case-insensitive)."""
    return enemy_map.get(name.lower(), 0)


def fetch_player_resets_from_site(name: str) -> int:
    """Busca resets de um player diretamente no site do amonOT (para hunted list)."""
    try:
        from bs4 import BeautifulSoup
        url = f"https://amonot.online/characters?name={requests.utils.quote(name)}"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return 0
        soup = BeautifulSoup(r.text, "html.parser")
        # Procura o campo de resets na página
        for el in soup.find_all(string=True):
            if "resets" in el.lower():
                import re
                m = re.search(r'(\d+)\s*reset', el, re.IGNORECASE)
                if m:
                    return int(m.group(1))
        return 0
    except Exception as e:
        print(f"[kill_ranking] erro ao buscar resets de {name}: {e}")
        return 0


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[kill_ranking] ⚠ Supabase não configurado")
        return

    # Carrega guild_data.json
    try:
        with open("guild_data.json", encoding="utf-8") as f:
            guild_data = json.load(f)
    except Exception as e:
        print(f"[kill_ranking] erro ao ler guild_data.json: {e}")
        return

    # Monta mapa de resets dos membros LP
    members_map = {}
    for m in guild_data.get("members", []):
        members_map[m["name"].lower()] = m.get("resets", 0)

    # Monta mapa de resets dos inimigos
    enemy_map = {}
    for eg in guild_data.get("enemy_guilds", []):
        for m in eg.get("members", []):
            enemy_map[m["name"].lower()] = m.get("resets", 0)

    # Monta set de nomes de membros LP (para validar killedBy e maiorDano)
    lp_members = set(members_map.keys())

    # Monta hunted list
    hunted_list = set()
    for h in guild_data.get("hunted_list", []):
        name = (h.get("name") or h if isinstance(h, str) else "").lower()
        if name:
            hunted_list.add(name)

    # Enemy set (guild + hunted)
    enemy_names = set(enemy_map.keys()) | hunted_list

    # Processa kills do histórico
    kills = guild_data.get("war_history", {}).get("all_kills", [])
    print(f"[kill_ranking] {len(kills)} kills no histórico")

    processed = 0
    skipped   = 0
    batch     = []

    for kill in kills:
        victim    = kill.get("player", "")
        killer    = kill.get("killedBy", "")
        assist    = kill.get("maiorDano", "")
        kill_time = kill.get("time", "")

        # Só processa se killer é membro da LP
        if killer.lower() not in lp_members:
            skipped += 1
            continue

        # Só processa se vítima é inimigo (guilda inimiga ou hunted list)
        if victim.lower() not in enemy_names:
            skipped += 1
            continue

        # Verifica se já processou
        if already_processed(killer, victim, kill_time):
            skipped += 1
            continue

        # Resets do killer
        killer_resets = get_member_resets(killer, members_map)

        # Resets da vítima
        victim_resets = get_enemy_resets(victim, enemy_map)
        if victim_resets == 0 and victim.lower() in hunted_list:
            victim_resets = fetch_player_resets_from_site(victim)

        # Calcula pontos da kill principal
        points = calc_points(killer_resets, victim_resets)

        # Converte kill_time para ISO
        try:
            dt = datetime.strptime(kill_time.strip(), "%b %d, %Y %H:%M")
            dt = dt.replace(tzinfo=timezone(timedelta(hours=-3)))
            kill_time_iso = dt.isoformat()
        except:
            kill_time_iso = kill_time

        batch.append({
            "killer":        killer,
            "victim":        victim,
            "killer_resets": killer_resets,
            "victim_resets": victim_resets,
            "points":        points,
            "is_assist":     False,
            "kill_time":     kill_time_iso,
        })

        # Assist: maiorDano é membro da LP (exceto se for o próprio killer)
        if assist and assist.lower() in lp_members and assist.lower() != killer.lower():
            batch.append({
                "killer":        assist,
                "victim":        victim,
                "killer_resets": get_member_resets(assist, members_map),
                "victim_resets": victim_resets,
                "points":        2,
                "is_assist":     True,
                "kill_time":     kill_time_iso,
            })

        processed += 1

    # Insere em lote
    if batch:
        ok = supa_post("kill_rankings", batch)
        if ok:
            print(f"[kill_ranking] ✅ {len(batch)} registros inseridos ({processed} kills)")
        else:
            print(f"[kill_ranking] ❌ erro ao inserir")
    else:
        print(f"[kill_ranking] ℹ nenhuma kill nova para processar ({skipped} ignoradas)")


if __name__ == "__main__":
    run()
