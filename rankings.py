"""
rankings.py
Monta rankings internos da guilda a partir dos dados de membros e personagens.
Gera highscores de resets, skills e exp.
"""


def build_rankings(members: list, char_stats: list) -> dict:
    """
    Recebe lista de membros (members.py) e stats de personagens (characters.py).
    Retorna dict com rankings por resets e level.
    """

    # ── Resets ───────────────────────────────────────────────────────────────
    resets_ranking = sorted(
        [{"name": m["name"], "value": m["resets"], "vocation": m.get("vocation", ""), "online": m.get("online", False)}
         for m in members],
        key=lambda x: x["value"],
        reverse=True,
    )

    # ── Level ─────────────────────────────────────────────────────────────────
    level_ranking = sorted(
        [{"name": m["name"], "value": m["level"], "vocation": m.get("vocation", ""), "online": m.get("online", False)}
         for m in members if m["level"] > 8],
        key=lambda x: x["value"],
        reverse=True,
    )

    return {
        "resets": {
            "name":      "Resets",
            "val_label": "Resets",
            "entries":   resets_ranking,
        },
        "level": {
            "name":      "Level",
            "val_label": "Level",
            "entries":   level_ranking,
        },
    }


def build_war_rankings(war_stats: dict) -> dict:
    """
    Monta rankings de guerra a partir das estatísticas do war.py.
    """
    return {
        "top_killers_mine": {
            "name":      "Top Fraggadores",
            "val_label": "Kills",
            "entries":   [
                {"name": p["name"], "value": p["kills"], "kills_today": p["kills_today"], "kills_week": p["kills_week"]}
                for p in war_stats.get("top_my_killers", [])
            ],
        },
        "top_killers_enemy": {
            "name":      "Top Inimigos",
            "val_label": "Kills",
            "entries":   [
                {"name": p["name"], "value": p["kills"], "kills_today": p["kills_today"], "kills_week": p["kills_week"]}
                for p in war_stats.get("top_enemy_killers", [])
            ],
        },
    }


# ── Teste direto ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    from members import fetch_guild_members
    from characters import fetch_all_characters

    MY_GUILD = "Lowly People"

    print("=== Buscando membros ===")
    r = fetch_guild_members(MY_GUILD)
    if not r["ok"]:
        print(f"Erro: {r['error']}")
        exit(1)

    members = r["members"]

    # Testa só com 5 membros para não demorar
    print("\n=== Buscando stats de 5 membros ===")
    sample = [m for m in members if not True][:5]  # começa vazio, usa cache se existir

    # Tenta carregar characters_test.json se existir
    try:
        with open("characters_test.json", encoding="utf-8") as f:
            char_stats = json.load(f)
        print(f"   Carregado do cache: {len(char_stats)} personagens")
    except FileNotFoundError:
        print("   Buscando 5 personagens...")
        char_stats = fetch_all_characters(members[:5])

    print("\n=== Montando rankings ===")
    rankings = build_rankings(members, char_stats)

    print(f"\n✅ Rankings gerados:")
    for key, r in rankings.items():
        entries = r["entries"]
        print(f"\n  📊 {r['name']} ({len(entries)} entradas)")
        for i, e in enumerate(entries[:3], 1):
            medal = ["🥇","🥈","🥉"][i-1]
            print(f"    {medal} {e['name']:25} {e['value']}")

    # Testa rankings de guerra se war_test.json existir
    try:
        with open("war_test.json", encoding="utf-8") as f:
            war_data = json.load(f)
        war_rankings = build_war_rankings(war_data["war"])
        print(f"\n⚔  Rankings de guerra:")
        for key, r in war_rankings.items():
            print(f"\n  {r['name']}:")
            for i, e in enumerate(r["entries"][:3], 1):
                medal = ["🥇","🥈","🥉"][i-1] if i <= 3 else "  "
                print(f"    {medal} {e['name']:25} {e['value']} kills")
    except FileNotFoundError:
        print("\n   (war_test.json não encontrado, pulando rankings de guerra)")

    with open("rankings_test.json", "w", encoding="utf-8") as f:
        json.dump(rankings, f, ensure_ascii=False, indent=2)
    print("\n💾 Resultado salvo em rankings_test.json")
