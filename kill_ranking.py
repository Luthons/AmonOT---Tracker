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

def calc_points(killer_resets: int, victim_resets: int, is_hunted: bool = False, is_bonus: bool = False) -> int:
    """
    Lógica de pontuação:
    - Vítima < 30 resets → 1 ponto base
    - Vítima >= 30 resets → (victim_resets - 30) + 1 pontos base
    - Killer < 30 resets → dobra os pontos
    - Vítima na hunted list → dobra os pontos
    - Vítima na lista bônus → dobra os pontos
    Multiplicadores acumulam.
    """
    if victim_resets < 30:
        base = 1
    else:
        base = (victim_resets - 30) + 1

    mult = 1
    if killer_resets < 30: mult *= 2
    if is_hunted:           mult *= 2
    if is_bonus:            mult *= 2

    return base * mult


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


def load_processed_cache() -> set:
    """Carrega todas as kills já processadas de uma vez. Retorna set de (killer, victim, kill_time)."""
    rows = supa_get("kill_rankings", {
        "select": "killer,victim,kill_time",
        "limit":  "10000",
    })
    return {(r["killer"], r["victim"], r["kill_time"]) for r in rows}


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

def get_bonus_list() -> set:
    """Busca a lista de personagens com pontos bonus."""
    rows = supa_get("kill_bonus_list", {"select": "char_name"})
    return {r["char_name"].lower() for r in rows}


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

    # Monta hunted list do Supabase
    hunted_rows = supa_get("hunted_list", {"select": "name"})
    hunted_list = {r["name"].lower() for r in hunted_rows}
    print(f"[kill_ranking] {len(hunted_list)} players na hunted list")

    # Enemy set (guild + hunted)
    enemy_names = set(enemy_map.keys()) | hunted_list

    # Busca lista bônus do Supabase
    bonus_list = get_bonus_list()
    print(f"[kill_ranking] {len(bonus_list)} personagens na lista bônus")

    # Processa kills do histórico
    kills = guild_data.get("war_history", {}).get("all_kills", [])
    print(f"[kill_ranking] {len(kills)} kills no histórico")

    processed = 0
    skipped   = 0
    batch     = []

    # Carrega kills já processadas de uma vez — zero queries por kill
    processed_cache = load_processed_cache()
    print(f"[kill_ranking] {len(processed_cache)} kills já no banco")

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

        # Converte kill_time para ISO antes de tudo
        try:
            dt = datetime.strptime(kill_time.strip(), "%b %d, %Y %H:%M")
            dt = dt.replace(tzinfo=timezone(timedelta(hours=-3)))
            kill_time_iso = dt.isoformat()
        except:
            kill_time_iso = kill_time

        # Verifica se já processou (em memória, sem query)
        if (killer, victim, kill_time_iso) in processed_cache:
            skipped += 1
            continue

        # Resets do killer
        killer_resets = get_member_resets(killer, members_map)

        # Resets da vítima
        victim_resets = get_enemy_resets(victim, enemy_map)
        if victim_resets == 0 and victim.lower() in hunted_list:
            victim_resets = fetch_player_resets_from_site(victim)

        # Calcula pontos da kill principal
        is_hunted = victim.lower() in hunted_list
        is_bonus  = victim.lower() in bonus_list
        points = calc_points(killer_resets, victim_resets, is_hunted, is_bonus)

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
            # Try inserting one by one to find the problematic record
            print(f"[kill_ranking] ❌ erro no lote, tentando um por um...")
            for record in batch:
                ok2 = supa_post("kill_rankings", [record])
                if not ok2:
                    print(f"[kill_ranking] ❌ falhou: {record}")
    else:
        print(f"[kill_ranking] ℹ nenhuma kill nova para processar ({skipped} ignoradas)")


if __name__ == "__main__":
    run()
