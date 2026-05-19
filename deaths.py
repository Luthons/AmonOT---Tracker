"""
deaths.py
Busca mortes PvP do mundo Baiak no AmonOT.
Retorna lista de eventos com: time, player, level, killedBy, maiorDano, world, isPvp
"""

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
}


def fetch_deaths(pages: int = 4, world: str = "Baiak", kill_type: str = "pvp") -> dict:
    """
    Busca mortes das últimas N páginas do lastkills.
    Retorna dict com: ok, deaths (lista completa), total
    """
    type_param  = f"&type={kill_type}" if kill_type else ""
    world_param = f"&world={world}"    if world     else ""

    all_deaths = []

    for p in range(1, pages + 1):
        page_param = f"&p={p}" if p > 1 else ""
        url = f"https://amonot.online/index.php?page=lastkills{world_param}{type_param}{page_param}"
        print(f"[deaths] buscando página {p}: {url}")

        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            print(f"[deaths] status HTTP: {r.status_code} | tamanho: {len(r.text)} bytes")
        except Exception as e:
            print(f"[deaths] erro na página {p}: {e}")
            continue

        if r.status_code != 200:
            print(f"[deaths] página {p} retornou HTTP {r.status_code}, pulando")
            continue

        deaths = _parse_deaths(r.text)
        print(f"[deaths]  → {len(deaths)} mortes encontradas")
        all_deaths.extend(deaths)

    return {
        "ok":     True,
        "deaths": all_deaths,
        "total":  len(all_deaths),
    }


def _parse_deaths(html: str) -> list:
    """Extrai mortes de uma página de lastkills."""
    soup   = BeautifulSoup(html, "html.parser")
    rows   = soup.select("div.char-table-row")
    deaths = []

    for row in rows:
        links = row.select("a[href*=characters]")
        if not links:
            continue

        children = [c.get_text(strip=True) for c in row.children if c.get_text(strip=True)]
        if len(children) < 4:
            continue

        time_text = children[0]
        player    = links[0].get_text(strip=True)
        world     = children[-1]

        try:
            level = int(children[2])
        except (ValueError, IndexError):
            level = 0

        # Killer principal e maior dano
        if len(links) >= 3:
            killer     = links[1].get_text(strip=True)
            maior_dano = links[2].get_text(strip=True)
        elif len(links) == 2:
            killer     = links[1].get_text(strip=True)
            maior_dano = ""
        else:
            killer     = children[3] if len(children) > 3 else ""
            maior_dano = ""

        deaths.append({
            "time":      time_text,
            "player":    player,
            "level":     level,
            "killedBy":  killer,
            "maiorDano": maior_dano,
            "world":     world,
            "isPvp":     len(links) >= 2,
        })

    return deaths


def filter_guild_deaths(deaths: list, my_members: set, enemy_members: set) -> dict:
    """
    Cruza lista de mortes com membros das duas guildas.
    Retorna my_kills e my_deaths separados.
    my_kills  = inimigos mortos por membros da minha guilda
    my_deaths = meus membros mortos por inimigos
    """
    my_kills  = []
    my_deaths = []

    for d in deaths:
        p  = d["player"].lower()
        k  = d["killedBy"].lower()
        md = (d["maiorDano"] or "").lower()

        p_is_mine   = p  in my_members
        p_is_enemy  = p  in enemy_members
        k_is_mine   = k  in my_members  or md in my_members
        k_is_enemy  = k  in enemy_members or md in enemy_members

        if p_is_enemy and k_is_mine:
            killer = d["killedBy"] if k in my_members else d["maiorDano"]
            my_kills.append({**d, "killedBy": killer})

        elif p_is_mine and k_is_enemy:
            killer = d["killedBy"] if k in enemy_members else d["maiorDano"]
            my_deaths.append({**d, "killedBy": killer})

    return {
        "my_kills":  my_kills,
        "my_deaths": my_deaths,
    }


# ── Teste direto ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json

    # 1. Busca mortes
    result = fetch_deaths(pages=4, world="Baiak", kill_type="pvp")
    print(f"\n✅ {result['total']} mortes encontradas no total")

    # 2. Simula cruzamento com membros conhecidos
    MY_MEMBERS = {
        "salles on amonot", "brunaobringer", "jotabringer", "wrathbringer",
        "valdim", "charles", "gizao", "haony", "judas", "niju", "zannk",
    }
    ENEMY_MEMBERS = {
        "sauron knight", "maguinn returns", "punish claude", "gbr originall",
        "voltoks", "ljxiv", "loke", "parmegiana", "doczz", "qiiso camisadez",
        "rei dfzera", "pala de sandalia",
    }

    crossed = filter_guild_deaths(result["deaths"], MY_MEMBERS, ENEMY_MEMBERS)

    print(f"\n⚔  Kills da Lowly People: {len(crossed['my_kills'])}")
    for k in crossed["my_kills"][:5]:
        print(f"   💀 {k['player']:25} morto por {k['killedBy']} ({k['time']})")

    print(f"\n💀 Deaths da Lowly People: {len(crossed['my_deaths'])}")
    for d in crossed["my_deaths"][:5]:
        print(f"   ☠  {d['player']:25} morto por {d['killedBy']} ({d['time']})")

    # Salva resultado
    with open("deaths_test.json", "w", encoding="utf-8") as f:
        json.dump({**result, **crossed}, f, ensure_ascii=False, indent=2)
    print("\n💾 Resultado salvo em deaths_test.json")
