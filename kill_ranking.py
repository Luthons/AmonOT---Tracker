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
        print(f"[kill_ranking] GET {table} falhou {r.status_code}: {r.text[:300]}")
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
        if r.status_code in (200, 201, 204):
            return True
        print(f"[kill_ranking] POST {table} falhou {r.status_code}: {r.text[:500]}")
        return False
    except Exception as e:
        print(f"[kill_ranking] erro Supabase POST: {e}")
        return False


def parse_kill_time(kill_time: str) -> str:
    """
    Converte kill_time para ISO 8601 em UTC.
    O site exibe horários em Brasília (UTC-3), então somamos 3h para obter UTC.
    Retorna a string original em caso de erro.

    Exemplo: "May 31, 2026 23:56" → "2026-06-01T02:56:00+00:00"

    IMPORTANTE: o Postgres armazena timestamptz sempre em UTC e normaliza
    qualquer timezone recebido. Salvar em UTC garante que o cache bata com
    o que o banco retorna, evitando re-inserções por mismatch de timezone.
    """
    try:
        dt = datetime.strptime(kill_time.strip(), "%b %d, %Y %H:%M")
        # Interpreta como Brasília (UTC-3) e converte para UTC
        dt = dt.replace(tzinfo=timezone(timedelta(hours=-3)))
        dt = dt.astimezone(timezone.utc)
        return dt.isoformat()
    except Exception:
        return kill_time


def normalize_to_utc(kill_time_str: str) -> str:
    """
    Normaliza qualquer string ISO 8601 com timezone para UTC.
    Necessário para comparar o cache (que vem do banco em UTC +00:00)
    com os valores gerados pelo parse_kill_time.
    Retorna a string original se não conseguir parsear.
    """
    try:
        dt = datetime.fromisoformat(kill_time_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat()
    except Exception:
        return kill_time_str


def load_processed_cache() -> set:
    """
    Carrega todas as kills já processadas de uma vez.
    Retorna set de (killer, victim, kill_time_utc) com timestamps normalizados para UTC,
    garantindo que a comparação com parse_kill_time() funcione independente do formato
    em que os registros foram originalmente inseridos.
    """
    rows = supa_get("kill_rankings", {
        "select": "killer,victim,kill_time",
        "limit":  "10000",
    })
    return {(r["killer"], r["victim"], normalize_to_utc(r["kill_time"])) for r in rows}


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

        # Converte kill_time para UTC uma única vez
        kill_time_iso = parse_kill_time(kill_time)

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
        print(f"[kill_ranking] tentando inserir lote de {len(batch)} registros ({processed} kills)...")
        ok = supa_post("kill_rankings", batch)
        if ok:
            print(f"[kill_ranking] ✅ {len(batch)} registros inseridos ({processed} kills)")
        else:
            print(f"[kill_ranking] ❌ lote falhou (ver erro acima). Abortando — NÃO tentando um por um para evitar timeout.")
            print(f"[kill_ranking] ℹ Verifique o erro do Supabase acima e corrija a constraint antes de re-rodar.")
    else:
        print(f"[kill_ranking] ℹ nenhuma kill nova para processar ({skipped} ignoradas)")


if __name__ == "__main__":
    run()
