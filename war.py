"""
war.py
Gerencia o histórico acumulado de guerra entre duas guildas.
Cruza mortes novas com o histórico salvo, nunca perde dados antigos.
"""

import json
from datetime import datetime, timezone, timedelta


# ── Fuso horário Brasília ─────────────────────────────────────────────────────

BRASILIA = timezone(timedelta(hours=-3))


def brasilia_now() -> datetime:
    return datetime.now(BRASILIA)


def parse_event_time(time_str: str) -> datetime:
    """Converte string do site (ex: 'May 19, 2026 00:08') em datetime com fuso Brasília."""
    try:
        dt = datetime.strptime(time_str.strip(), "%b %d, %Y %H:%M")
        return dt.replace(tzinfo=BRASILIA)
    except Exception:
        return brasilia_now()


def day_start() -> datetime:
    """Início do dia da guilda: 22h do dia anterior (horário Brasília)."""
    now = brasilia_now()
    today_22 = now.replace(hour=22, minute=0, second=0, microsecond=0)
    if now < today_22:
        today_22 -= timedelta(days=1)
    return today_22


def week_start() -> datetime:
    """Início da semana: 7 dias atrás a partir do day_start."""
    return day_start() - timedelta(days=6)


# ── Chave única por evento ────────────────────────────────────────────────────

def event_key(event: dict) -> str:
    """Gera chave única para um evento de morte, evitando duplicatas."""
    return f"{event['player']}|{event['time']}"


# ── Merge com histórico ───────────────────────────────────────────────────────

def merge_war_history(previous_war: dict, new_kills: list, new_deaths: list) -> dict:
    """
    Recebe o histórico anterior e os eventos novos.
    Adiciona só eventos que ainda não existem no histórico.
    Retorna o histórico atualizado.
    """
    # Carrega histórico anterior
    prev_kills  = previous_war.get("all_kills",  [])
    prev_deaths = previous_war.get("all_deaths", [])

    # Monta sets de chaves já vistas
    seen_kills  = {event_key(e) for e in prev_kills}
    seen_deaths = {event_key(e) for e in prev_deaths}

    # Adiciona só eventos novos
    added_kills  = 0
    added_deaths = 0

    for e in new_kills:
        if event_key(e) not in seen_kills:
            prev_kills.append(e)
            seen_kills.add(event_key(e))
            added_kills += 1

    for e in new_deaths:
        if event_key(e) not in seen_deaths:
            prev_deaths.append(e)
            seen_deaths.add(event_key(e))
            added_deaths += 1

    print(f"[war] +{added_kills} kills novos | +{added_deaths} deaths novos")
    print(f"[war] histórico total: {len(prev_kills)} kills | {len(prev_deaths)} deaths")

    # Ordena por tempo (mais recente primeiro)
    prev_kills.sort(key=lambda e: e["time"], reverse=True)
    prev_deaths.sort(key=lambda e: e["time"], reverse=True)

    return {
        "all_kills":  prev_kills,
        "all_deaths": prev_deaths,
    }


# ── Cálculo de estatísticas ───────────────────────────────────────────────────

def compute_war_stats(all_kills: list, all_deaths: list, my_guild: str, enemy_guild: str) -> dict:
    """
    Calcula todas as estatísticas de guerra a partir do histórico completo.
    """
    day   = day_start()
    week  = week_start()

    def is_today(e):
        return parse_event_time(e["time"]) >= day

    def is_week(e):
        return parse_event_time(e["time"]) >= week

    # Totais gerais
    kills_total  = len(all_kills)
    deaths_total = len(all_deaths)

    # Totais por período
    kills_today  = sum(1 for e in all_kills  if is_today(e))
    deaths_today = sum(1 for e in all_deaths if is_today(e))
    kills_week   = sum(1 for e in all_kills  if is_week(e))
    deaths_week  = sum(1 for e in all_deaths if is_week(e))

    # Top fraggadores da minha guilda (quem mais matou)
    my_killers = {}
    for e in all_kills:
        k = e["killedBy"]
        if k not in my_killers:
            my_killers[k] = {"name": k, "kills": 0, "kills_today": 0, "kills_week": 0}
        my_killers[k]["kills"] += 1
        if is_today(e): my_killers[k]["kills_today"] += 1
        if is_week(e):  my_killers[k]["kills_week"]  += 1

    # Top fraggadores do inimigo (quem mais nos matou)
    enemy_killers = {}
    for e in all_deaths:
        k = e["killedBy"]
        if k not in enemy_killers:
            enemy_killers[k] = {"name": k, "kills": 0, "kills_today": 0, "kills_week": 0}
        enemy_killers[k]["kills"] += 1
        if is_today(e): enemy_killers[k]["kills_today"] += 1
        if is_week(e):  enemy_killers[k]["kills_week"]  += 1

    # Ranking de quem mais morreu (deaths por membro)
    my_deaths_by_player = {}
    for e in all_deaths:
        p = e["player"]
        my_deaths_by_player[p] = my_deaths_by_player.get(p, 0) + 1

    enemy_deaths_by_player = {}
    for e in all_kills:
        p = e["player"]
        enemy_deaths_by_player[p] = enemy_deaths_by_player.get(p, 0) + 1

    # Ordena rankings
    top_my_killers     = sorted(my_killers.values(),      key=lambda x: x["kills"], reverse=True)[:10]
    top_enemy_killers  = sorted(enemy_killers.values(),   key=lambda x: x["kills"], reverse=True)[:10]

    # War log (feed de eventos recentes — últimos 100 eventos misturados e ordenados)
    war_log = []
    for e in all_kills:
        war_log.append({**e, "type": "kill"})
    for e in all_deaths:
        war_log.append({**e, "type": "death"})
    war_log.sort(key=lambda e: e["time"], reverse=True)
    war_log = war_log[:100]

    # ── Streak de vantagem ────────────────────────────────────────────────────
    # Conta dias consecutivos onde LP teve mais kills que deaths (K/D > 1)
    # Agrupa eventos por dia e compara kills vs deaths em cada dia
    days_kills  = {}
    days_deaths = {}
    for e in all_kills:
        try:
            day = parse_event_time(e["time"]).strftime("%Y-%m-%d")
            days_kills[day] = days_kills.get(day, 0) + 1
        except: pass
    for e in all_deaths:
        try:
            day = parse_event_time(e["time"]).strftime("%Y-%m-%d")
            days_deaths[day] = days_deaths.get(day, 0) + 1
        except: pass

    # Dia atual (ainda em andamento) — não entra no streak
    # O "dia atual" começa às 22h de Brasília
    current_day_start = day_start()
    current_day_key   = current_day_start.strftime("%Y-%m-%d")

    all_days = sorted(set(list(days_kills.keys()) + list(days_deaths.keys())), reverse=True)
    streak = 0
    for d_key in all_days:
        # Ignora o dia atual (ainda não fechou)
        if d_key == current_day_key:
            continue
        k = days_kills.get(d_key, 0)
        d = days_deaths.get(d_key, 0)
        # Ignora dias sem nenhuma atividade (não quebra nem conta)
        if k == 0 and d == 0:
            continue
        # Dia com atividade: se LP ganhou, conta; senão, quebra
        if k > d:
            streak += 1
        else:
            break

    return {
        "my_guild":             my_guild,
        "enemy_guild":          enemy_guild,
        "updated_at":           brasilia_now().strftime("%d/%m/%Y às %H:%M (Brasília)"),

        # Totais
        "kills_total":          kills_total,
        "deaths_total":         deaths_total,
        "kills_today":          kills_today,
        "deaths_today":         deaths_today,
        "kills_week":           kills_week,
        "deaths_week":          deaths_week,

        # K/D
        "kd_total":             round(kills_total  / deaths_total,  2) if deaths_total  else None,
        "kd_today":             round(kills_today  / deaths_today,  2) if deaths_today  else None,
        "kd_week":              round(kills_week   / deaths_week,   2) if deaths_week   else None,

        # Streak
        "streak_days":          streak,

        # Rankings
        "top_my_killers":       top_my_killers,
        "top_enemy_killers":    top_enemy_killers,
        "my_deaths_by_player":  sorted(my_deaths_by_player.items(),    key=lambda x: x[1], reverse=True)[:10],
        "enemy_deaths_by_player": sorted(enemy_deaths_by_player.items(), key=lambda x: x[1], reverse=True)[:10],

        # Feed
        "war_log":              war_log,
    }


def load_previous_war(path: str) -> dict:
    """Carrega histórico de guerra do arquivo JSON anterior."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data.get("war_history", {})
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"[war] nenhum histórico anterior encontrado em '{path}', iniciando do zero")
        return {}


# ── Teste direto ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from deaths import fetch_deaths, filter_guild_deaths
    from members import fetch_guild_members

    MY_GUILD    = "Lowly People"
    ENEMY_GUILD = "MentaliTY"

    # 1. Busca membros das duas guildas
    print("=== Buscando membros ===")
    r_mine  = fetch_guild_members(MY_GUILD)
    r_enemy = fetch_guild_members(ENEMY_GUILD)

    if not r_mine["ok"] or not r_enemy["ok"]:
        print("Erro ao buscar membros")
        exit(1)

    my_set    = {m["name"].lower() for m in r_mine["members"]}
    enemy_set = {m["name"].lower() for m in r_enemy["members"]}

    # 2. Busca mortes
    print("\n=== Buscando mortes ===")
    deaths_result = fetch_deaths(pages=4, world="Baiak", kill_type="pvp")
    crossed       = filter_guild_deaths(deaths_result["deaths"], my_set, enemy_set)

    # 3. Carrega histórico anterior (se existir)
    print("\n=== Carregando histórico anterior ===")
    previous_war = load_previous_war("war_test.json")

    # 4. Merge com histórico
    print("\n=== Mergeando histórico ===")
    history = merge_war_history(previous_war, crossed["my_kills"], crossed["my_deaths"])

    # 5. Calcula estatísticas
    print("\n=== Calculando estatísticas ===")
    stats = compute_war_stats(
        history["all_kills"],
        history["all_deaths"],
        MY_GUILD,
        ENEMY_GUILD,
    )

    # 6. Salva
    output = {
        "war_history": history,
        "war":         stats,
    }

    with open("war_test.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Guerra: {stats['kills_total']} kills | {stats['deaths_total']} deaths")
    print(f"   Hoje:   {stats['kills_today']} kills | {stats['deaths_today']} deaths")
    print(f"   K/D:    {stats['kd_total']}")
    print(f"\n🗡  Top fraggadores da {MY_GUILD}:")
    for p in stats["top_my_killers"][:5]:
        print(f"   {p['name']:25} {p['kills']} kills ({p['kills_today']} hoje)")
    print(f"\n💀 Top fraggadores da {ENEMY_GUILD}:")
    for p in stats["top_enemy_killers"][:5]:
        print(f"   {p['name']:25} {p['kills']} kills ({p['kills_today']} hoje)")

    print("\n💾 Resultado salvo em war_test.json")
