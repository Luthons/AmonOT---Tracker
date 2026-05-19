"""
characters.py
Busca stats individuais de cada personagem no AmonOT.
Retorna: level, resets, vocation, skills, last_login, guild_rank
"""

import time
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

DELAY_BETWEEN_REQUESTS = 0.5  # segundos entre cada request


def fetch_character(name: str) -> dict:
    """
    Busca stats de um personagem pelo nome.
    Retorna dict com todos os dados disponíveis.
    """
    url = f"https://amonot.online/characters?name={requests.utils.quote(name)}"

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
    except Exception as e:
        return {"ok": False, "name": name, "error": str(e)}

    if r.status_code != 200:
        return {"ok": False, "name": name, "error": f"HTTP {r.status_code}"}

    soup = BeautifulSoup(r.text, "html.parser")

    # ── Verifica se o perfil está oculto ─────────────────────────────────────
    hidden = False
    for span in soup.find_all("span"):
        if "(hidden)" in span.get_text(strip=True).lower():
            hidden = True
            break
    details = {}
    for row in soup.select(".char-detail-row"):
        label = row.select_one(".char-detail-label")
        value = row.select_one(".char-detail-value")
        if label and value:
            key = label.get_text(strip=True)
            val = value.get_text(strip=True)
            details[key] = val

    # Level (vem com vírgula, ex: "1,022")
    level_raw = details.get("Level", "0").replace(",", "")
    try:
        level = int(level_raw)
    except ValueError:
        level = 0

    # Resets
    try:
        resets = int(details.get("Resets", "0"))
    except ValueError:
        resets = 0

    # Vocação
    vocation = details.get("Vocação", details.get("Vocation", ""))

    # Último login
    last_login = details.get("Último Login", details.get("Last Login", ""))

    # Guild e cargo
    guild_raw  = details.get("Guild", "")
    guild_rank = ""
    guild_name = ""
    if guild_raw:
        # Ex: "The Leader daLowly People" — "da/de/do" colado antes do nome da guilda
        import re
        # Só separa no "da" que está colado diretamente a uma letra maiúscula (início do nome da guilda)
        match = re.search(r'(da|do)(?=[A-Z])', guild_raw)
        if match:
            guild_rank = guild_raw[:match.start()].strip()
            guild_name = guild_raw[match.end():].strip()
        else:
            guild_name = guild_raw

    # Nome anterior
    former_names = details.get("Nomes Anteriores", details.get("Former Names", ""))

    # ── Skills via .skill-item ────────────────────────────────────────────────
    return {
        "ok":           True,
        "name":         name,
        "hidden":       hidden,
        "level":        level,
        "resets":       resets,
        "vocation":     vocation,
        "last_login":   last_login,
        "guild_name":   guild_name,
        "guild_rank":   guild_rank,
        "former_names": former_names,
    }


def fetch_all_characters(members: list, delay: float = DELAY_BETWEEN_REQUESTS) -> list:
    """
    Busca stats de todos os membros com delay entre cada request.
    Retorna lista de dicts com os stats de cada um.
    """
    results = []
    total   = len(members)

    for i, member in enumerate(members, 1):
        name = member if isinstance(member, str) else member["name"]
        print(f"[characters] ({i}/{total}) {name}")
        result = fetch_character(name)
        results.append(result)
        if i < total:
            time.sleep(delay)

    ok_count   = sum(1 for r in results if r["ok"])
    fail_count = total - ok_count
    print(f"[characters] concluído: {ok_count} ok | {fail_count} falhas")

    return results


# ── Teste direto ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json

    # Testa com 3 membros para não demorar muito
    test_members = ["Salles On Amonot", "Brunaobringer", "Wrathbringer"]

    print(f"=== Testando {len(test_members)} personagens ===\n")
    results = fetch_all_characters(test_members, delay=0.5)

    for r in results:
        if r["ok"]:
            hidden_tag = " 🔒 HIDDEN" if r["hidden"] else ""
            print(f"\n✅ {r['name']}{hidden_tag}")
            print(f"   Level:    {r['level']} | Resets: {r['resets']} | Voc: {r['vocation']}")
            print(f"   Último login: {r['last_login']}")
            print(f"   Guild rank: {r['guild_rank']} | Guild: {r['guild_name']}")
            print(f"   Nomes anteriores: {r['former_names'] or '(nenhum)'}")
        else:
            print(f"\n❌ {r['name']}: {r['error']}")

    with open("characters_test.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n💾 Resultado salvo em characters_test.json")
