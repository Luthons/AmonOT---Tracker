"""
members.py
Busca membros de uma guilda no AmonOT.
Retorna lista com: name, level, resets, vocation, rank, status
"""

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
}


def fetch_guild_members(guild_name: str) -> dict:
    """
    Busca todos os membros de uma guilda.
    Retorna dict com: ok, members, online_count, offline_count, owner, founded
    """
    url = f"https://amonot.online/guilds?name={requests.utils.quote(guild_name)}&status=all"
    print(f"[members] buscando: {url}")

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        print(f"[members] status HTTP: {r.status_code} | tamanho: {len(r.text)} bytes")
    except Exception as e:
        return {"ok": False, "error": f"Erro de conexão: {e}", "members": []}

    if r.status_code != 200:
        return {"ok": False, "error": f"HTTP {r.status_code}", "members": []}

    soup = BeautifulSoup(r.text, "html.parser")
    rows = soup.select(".char-table-row")
    print(f"[members] rows encontradas: {len(rows)}")

    if not rows:
        return {"ok": False, "error": f"Guilda '{guild_name}' não encontrada ou sem membros.", "members": []}

    # Info geral da guilda (owner, founded)
    owner   = ""
    founded = ""
    for el in soup.select(".guild-info-item, .info-row, td"):
        text = el.get_text(strip=True)
        if "Líder:" in text or "Owner:" in text:
            owner = text.split(":")[-1].strip()
        if "Fundada" in text or "Founded" in text:
            founded = text.split(":")[-1].strip()

    members = []
    online_count  = 0
    offline_count = 0

    for row in rows:
        # Nome
        name_el = row.select_one('span[data-label="Nome"]')
        name = name_el.get_text(strip=True) if name_el else ""
        if not name:
            continue

        # Level
        level_el = row.select_one('span[data-label="Level"]')
        try:
            level = int(level_el.get_text(strip=True)) if level_el else 0
        except ValueError:
            level = 0

        # Resets
        resets_el = row.select_one('span[data-label="Resets"]')
        try:
            resets = int(resets_el.get_text(strip=True)) if resets_el else 0
        except ValueError:
            resets = 0

        # Vocação
        voc_el = row.select_one('span[data-label="Vocação"]')
        vocation = voc_el.get_text(strip=True) if voc_el else ""

        # Nick (apelido)
        nick_el = row.select_one('span[data-label="Nick"]')
        nick = nick_el.get_text(strip=True) if nick_el else ""

        # Status — o .badge é irmão do span[data-label="Status"], não filho
        badge_el = row.select_one(".badge")
        status_text = badge_el.get_text(strip=True).lower() if badge_el else "offline"
        is_online = "online" in status_text

        if is_online:
            online_count += 1
        else:
            offline_count += 1

        # Rank (cargo) — geralmente vem num span ou td separado
        rank_el = row.select_one('span[data-label="Cargo"], span[data-label="Rank"]')
        rank = rank_el.get_text(strip=True) if rank_el else "Member"

        members.append({
            "name":     name,
            "level":    level,
            "resets":   resets,
            "vocation": vocation,
            "nick":     nick,
            "rank":     rank,
            "online":   is_online,
        })

    print(f"[members] {len(members)} membros | {online_count} online | {offline_count} offline")

    return {
        "ok":            True,
        "members":       members,
        "online_count":  online_count,
        "offline_count": offline_count,
        "owner":         owner,
        "founded":       founded,
    }


# ── Teste direto ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json

    result = fetch_guild_members("Lowly People")

    if result["ok"]:
        print(f"\n✅ {len(result['members'])} membros encontrados")
        print(f"   Online: {result['online_count']} | Offline: {result['offline_count']}")
        print(f"   Owner: {result['owner'] or '(não encontrado)'}")
        print(f"   Fundada: {result['founded'] or '(não encontrado)'}")
        print("\n--- Primeiros 5 membros ---")
        for m in result["members"][:5]:
            status = "🟢" if m["online"] else "⚫"
            print(f"  {status} {m['name']:30} lv={m['level']:5} resets={m['resets']:3} {m['vocation']}")
    else:
        print(f"\n❌ Erro: {result['error']}")

    # Salva resultado completo pra inspeção
    with open("members_test.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("\n💾 Resultado salvo em members_test.json")
