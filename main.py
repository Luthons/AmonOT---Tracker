"""
main.py
Orquestra todos os módulos e gera o guild_data.json final.
Roda via GitHub Actions a cada hora.

Uso:
    python main.py
"""

import json
import os
import traceback
from datetime import datetime, timezone, timedelta

# ── Módulos ───────────────────────────────────────────────────────────────────
from members    import fetch_guild_members
from deaths     import fetch_deaths, filter_guild_deaths
from war        import merge_war_history, compute_war_stats, load_previous_war
from characters import fetch_all_characters
from history    import (
    update_reset_history,
    update_member_changes,
    update_hunted_list,
    compute_resets_today,
    load_history,
)
from rankings   import build_rankings, build_war_rankings

# ── Config ────────────────────────────────────────────────────────────────────

CONFIG_PATH     = "config.json"
OUTPUT_PATH     = "guild_data.json"
DEFAULT_CONFIG  = {
    "my_guild":      "Lowly People",
    "enemy_guilds":  ["MentaliTY"],
    "world":         "Baiak",
    "owner":         "Salles On Amonot",
    "founded":       "Abril de 2026",
    "deaths_pages":  4,
    "fetch_chars":   True,
}

BRASILIA = timezone(timedelta(hours=-3))


def brasilia_now() -> datetime:
    return datetime.now(BRASILIA)


def now_str() -> str:
    return brasilia_now().strftime("%d/%m/%Y às %H:%M (Brasília)")


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
            print(f"[main] config carregado: {CONFIG_PATH}")
            return {**DEFAULT_CONFIG, **cfg}
    except FileNotFoundError:
        print(f"[main] config.json não encontrado, usando padrão")
        return DEFAULT_CONFIG


def load_previous(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"[main] nenhum dado anterior em '{path}', iniciando do zero")
        return {}


def save_output(data: dict, path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[main] ✅ salvo em {path}")


# ── Pipeline principal ────────────────────────────────────────────────────────

def run():
    print(f"\n{'='*50}")
    print(f"  AmonOT Scraper — {now_str()}")
    print(f"{'='*50}\n")

    cfg      = load_config()
    previous = load_previous(OUTPUT_PATH)

    my_guild     = cfg["my_guild"]
    enemy_guilds = cfg["enemy_guilds"]
    world        = cfg["world"]
    pages        = cfg["deaths_pages"]

    errors = []

    # ── 1. Membros da minha guilda ────────────────────────────────────────────
    print("── [1/7] Membros da minha guilda ──")
    my_members_data = {"ok": False, "members": [], "online_count": 0, "offline_count": 0}
    try:
        my_members_data = fetch_guild_members(my_guild)
        if not my_members_data["ok"]:
            raise Exception(my_members_data.get("error", "erro desconhecido"))
        print(f"   ✅ {len(my_members_data['members'])} membros | {my_members_data['online_count']} online")
    except Exception as e:
        errors.append(f"Membros: {e}")
        print(f"   ❌ {e}")

    my_members = my_members_data["members"]
    my_set     = {m["name"].lower() for m in my_members}

    # ── 2. Membros das guildas inimigas ───────────────────────────────────────
    print("\n── [2/7] Membros das guildas inimigas ──")
    all_enemy_members = []
    enemy_guilds_data = {}

    for enemy_name in enemy_guilds:
        try:
            r = fetch_guild_members(enemy_name)
            if not r["ok"]:
                raise Exception(r.get("error", "erro desconhecido"))
            enemy_guilds_data[enemy_name] = r
            all_enemy_members.extend(r["members"])
            print(f"   ✅ {enemy_name}: {len(r['members'])} membros | {r['online_count']} online")
        except Exception as e:
            errors.append(f"Inimigos ({enemy_name}): {e}")
            print(f"   ❌ {enemy_name}: {e}")

    enemy_set = {m["name"].lower() for m in all_enemy_members}

    # ── 3. Mortes e guerra ────────────────────────────────────────────────────
    print("\n── [3/7] Mortes e guerra ──")
    new_kills  = []
    new_deaths = []
    war_stats  = previous.get("war", {})

    try:
        deaths_result = fetch_deaths(pages=pages, world=world, kill_type="pvp")
        crossed       = filter_guild_deaths(deaths_result["deaths"], my_set, enemy_set)
        new_kills     = crossed["my_kills"]
        new_deaths    = crossed["my_deaths"]
        print(f"   ✅ {deaths_result['total']} mortes analisadas | {len(new_kills)} kills | {len(new_deaths)} deaths novos")

        # Merge com histórico
        prev_war  = load_previous_war(OUTPUT_PATH)
        history   = merge_war_history(prev_war, new_kills, new_deaths)
        war_stats = compute_war_stats(
            history["all_kills"],
            history["all_deaths"],
            my_guild,
            ", ".join(enemy_guilds),
        )
    except Exception as e:
        errors.append(f"Guerra: {e}")
        print(f"   ❌ {e}")
        traceback.print_exc()
        history = previous.get("war_history", {"all_kills": [], "all_deaths": []})

    # ── 4. Stats individuais de personagens ───────────────────────────────────
    print("\n── [4/7] Stats individuais ──")
    char_stats = []

    if cfg.get("fetch_chars") and my_members:
        try:
            char_stats = fetch_all_characters(my_members, delay=0.4)
            ok_count   = sum(1 for c in char_stats if c.get("ok"))
            print(f"   ✅ {ok_count}/{len(my_members)} personagens coletados")
        except Exception as e:
            errors.append(f"Characters: {e}")
            print(f"   ❌ {e}")
    else:
        print("   ⏭ pulado (fetch_chars=false no config)")

    # ── 5. Histórico de resets e membros ─────────────────────────────────────
    print("\n── [5/7] Histórico ──")
    prev_history   = load_history(OUTPUT_PATH)
    reset_history  = {}
    member_changes = {}
    hunted_data    = {}
    resets_today   = []

    try:
        reset_history = update_reset_history(my_members, prev_history.get("resets", {}))
        resets_today  = compute_resets_today(my_members, reset_history)
        print(f"   ✅ Resets: {len(reset_history['events'])} eventos | {len(resets_today)} resets hoje")
    except Exception as e:
        errors.append(f"Reset history: {e}")
        print(f"   ❌ {e}")

    try:
        # Verifica se o histórico anterior era da guilda errada (nossa guilda)
        # Se a lista anterior contiver membros da nossa guilda, reseta o histórico
        prev_member_hist = prev_history.get("members", {})
        prev_list = set(prev_member_hist.get("last_member_list", []))
        my_names  = {m["name"] for m in my_members}
        # Se mais de 50% da lista anterior for da nossa guilda, reseta
        if prev_list and len(prev_list & my_names) / len(prev_list) > 0.5:
            print("   ⚠ Histórico de membros era da guilda errada, resetando...")
            prev_member_hist = {}
        member_changes = update_member_changes(all_enemy_members, prev_member_hist)
        recent = member_changes.get("recent_changes", [])
        print(f"   ✅ Mudanças guilda inimiga: {len(recent)} mudança(s) detectada(s)")
    except Exception as e:
        errors.append(f"Member changes: {e}")
        print(f"   ❌ {e}")

    try:
        hunted_data = update_hunted_list(
            all_enemy_members,
            history.get("all_deaths", []),
            prev_history.get("hunted", {}),
        )
        print(f"   ✅ Hunted list: {len(hunted_data['hunted'])} suspeitos")
    except Exception as e:
        errors.append(f"Hunted list: {e}")
        print(f"   ❌ {e}")

    # ── 6. Rankings ───────────────────────────────────────────────────────────
    print("\n── [6/7] Rankings ──")
    rankings     = {}
    war_rankings = {}

    try:
        rankings     = build_rankings(my_members, char_stats)
        war_rankings = build_war_rankings(war_stats)
        print(f"   ✅ Rankings gerados: {list(rankings.keys())}")
    except Exception as e:
        errors.append(f"Rankings: {e}")
        print(f"   ❌ {e}")

    # ── 7. Monta JSON final ───────────────────────────────────────────────────
    print("\n── [7/7] Montando JSON final ──")

    # Hunted list: merge automático + manual
    hunted_auto   = list(hunted_data.get("hunted", {}).values())
    hunted_manual = cfg.get("hunted_manual", [])

    # Marca cada entrada com sua origem
    for h in hunted_auto:
        h["source"] = "auto"
    for h in hunted_manual:
        h["source"] = "manual"

    # Combina sem duplicar (manual tem prioridade)
    manual_names  = {h["name"].lower() for h in hunted_manual}
    hunted_merged = hunted_manual + [h for h in hunted_auto if h["name"].lower() not in manual_names]

    # Histórico de resets por membro (para expand na tabela)
    reset_events = reset_history.get("events", [])
    reset_by_member = {}
    for ev in reset_events:
        name = ev["name"]
        if name not in reset_by_member:
            reset_by_member[name] = []
        reset_by_member[name].append({
            "resets":    ev["resets"],
            "gained":    ev["gained"],
            "timestamp": ev["timestamp"],
        })

    # Info de cada guilda inimiga para o frontend
    enemy_guilds_info = []
    for name, data in enemy_guilds_data.items():
        enemy_guilds_info.append({
            "name":          name,
            "total_members": len(data["members"]),
            "online_count":  data["online_count"],
            "offline_count": data["offline_count"],
            "members":       data["members"],
        })

    output = {
        # ── Info geral ──
        "name":          my_guild,
        "updated_at":    now_str(),
        "owner":         cfg.get("owner", ""),
        "founded":       cfg.get("founded", ""),
        "total_members": len(my_members),
        "online_count":  my_members_data.get("online_count", 0),
        "offline_count": my_members_data.get("offline_count", 0),

        # ── Membros (enriquecidos com chars + histórico de resets) ──
        "members": [
            {
                **m,
                **next(
                    ({"last_login": c["last_login"], "guild_rank": c["guild_rank"],
                      "former_names": c["former_names"], "hidden": c["hidden"]}
                     for c in char_stats if c.get("ok") and c["name"] == m["name"]),
                    {}
                ),
                "reset_history": reset_by_member.get(m["name"], []),
            }
            for m in my_members
        ],

        # ── Guildas inimigas ──
        "enemy_guilds": enemy_guilds_info,

        # ── Resets do dia ──
        "resets_today": resets_today,

        # ── Rankings ──
        "highscores":    rankings,
        "war_rankings":  war_rankings,

        # ── Guerra ──
        "war":         war_stats,
        "war_history": history,

        # ── Histórico interno ──
        "history": {
            "resets":  reset_history,
            "members": member_changes,
            "hunted":  hunted_data,
        },

        # ── Mudanças recentes de membros ──
        "recent_changes": member_changes.get("recent_changes", []),
        "changes_log":    member_changes.get("changes_log", [])[-50:],

        # ── Hunted list (auto + manual) ──
        "hunted_list": hunted_merged,

        # ── Erros da execução (para debug) ──
        "errors": errors,
    }

    save_output(output, OUTPUT_PATH)

    # ── Resumo final ──────────────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"  CONCLUÍDO — {now_str()}")
    print(f"  Membros: {len(my_members)} | Online: {my_members_data.get('online_count',0)}")
    print(f"  Guerra:  {war_stats.get('kills_total',0)} kills | {war_stats.get('deaths_total',0)} deaths | K/D: {war_stats.get('kd_total','—')}")
    print(f"  Resets hoje: {len(resets_today)}")
    if errors:
        print(f"  ⚠ {len(errors)} erro(s): {' | '.join(errors)}")
    else:
        print(f"  ✅ Sem erros")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    run()
