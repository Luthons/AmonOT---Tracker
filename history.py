"""
history.py
Rastreia histórico de resets, entradas/saídas de membros e hunted list.
Compara snapshot atual com o anterior para detectar mudanças.
"""

import json
from datetime import datetime, timezone, timedelta

BRASILIA = timezone(timedelta(hours=-3))


def brasilia_now() -> datetime:
    return datetime.now(BRASILIA)


def now_str() -> str:
    return brasilia_now().strftime("%d/%m/%Y às %H:%M (Brasília)")


def day_start() -> datetime:
    """Início do dia da guilda: 22h do dia anterior (horário Brasília)."""
    now = brasilia_now()
    today_22 = now.replace(hour=22, minute=0, second=0, microsecond=0)
    if now < today_22:
        today_22 -= timedelta(days=1)
    return today_22


# ── Histórico de resets ───────────────────────────────────────────────────────

def update_reset_history(current_members: list, previous_history: dict) -> dict:
    """
    Compara resets atuais com snapshot anterior.
    Detecta quem resetou desde a última execução.
    Acumula snapshots horários para calcular resets por período.
    """
    hour_key  = brasilia_now().strftime("%Y-%m-%d-%H")
    snapshots = previous_history.get("snapshots", {})
    events    = previous_history.get("events", [])

    # Mapa anterior de resets por nome
    prev_snapshot = previous_history.get("last_snapshot", {})

    new_events  = []
    new_snapshot = {}

    for member in current_members:
        name   = member["name"]
        resets = member["resets"]
        new_snapshot[name] = resets

        prev_resets = prev_snapshot.get(name)

        if prev_resets is not None and resets > prev_resets:
            gained = resets - prev_resets
            event  = {
                "name":       name,
                "resets":     resets,
                "gained":     gained,
                "timestamp":  now_str(),
                "hour_key":   hour_key,
            }
            new_events.append(event)
            events.append(event)
            print(f"[history] reset detectado: {name} +{gained} (total: {resets})")

    # Salva snapshot da hora atual
    snapshots[hour_key] = new_snapshot

    # Mantém apenas últimos 7 dias de snapshots (168 horas)
    if len(snapshots) > 168:
        oldest_keys = sorted(snapshots.keys())[:-168]
        for k in oldest_keys:
            del snapshots[k]

    # Mantém apenas últimos 30 dias de eventos
    cutoff = (brasilia_now() - timedelta(days=30)).strftime("%Y-%m-%d")
    events = [e for e in events if e.get("hour_key", "9999") >= cutoff.replace("-", "-")]

    print(f"[history] {len(new_events)} reset(s) novos detectados")

    return {
        "last_snapshot": new_snapshot,
        "snapshots":     snapshots,
        "events":        events,
    }


def compute_resets_today(members: list, reset_history: dict) -> list:
    """
    Calcula quantos resets cada membro fez hoje e nos últimos 7 dias.
    """
    day   = day_start()
    week  = day - timedelta(days=6)

    day_key  = day.strftime("%Y-%m-%d-%H")
    week_key = week.strftime("%Y-%m-%d-%H")

    snapshots = reset_history.get("snapshots", {})

    # Snapshot mais próximo do início do dia
    def find_snapshot_near(target_key: str) -> dict:
        keys = sorted(snapshots.keys())
        best = None
        for k in keys:
            if k <= target_key:
                best = k
        return snapshots.get(best, {}) if best else {}

    snap_day  = find_snapshot_near(day_key)
    snap_week = find_snapshot_near(week_key)

    result = []
    for member in members:
        name          = member["name"]
        current       = member["resets"]
        resets_today  = max(0, current - snap_day.get(name,  current))
        resets_week   = max(0, current - snap_week.get(name, current))

        if resets_today > 0 or resets_week > 0:
            result.append({
                "name":         name,
                "resets_total": current,
                "resets_today": resets_today,
                "resets_week":  resets_week,
                "vocation":     member.get("vocation", ""),
                "online":       member.get("online", False),
            })

    return sorted(result, key=lambda x: x["resets_today"], reverse=True)


def compute_guild_reset_stats(members: list, reset_history: dict, guild_name: str) -> dict:
    """
    Calcula estatísticas agregadas de resets da guilda por período.
    - Hoje, semana, mês e lifetime (desde início do monitoramento)
    - Média por player
    """
    day        = day_start()
    week_start = day - timedelta(days=6)
    month_start= day - timedelta(days=29)

    day_key   = day.strftime("%Y-%m-%d-%H")
    week_key  = week_start.strftime("%Y-%m-%d-%H")
    month_key = month_start.strftime("%Y-%m-%d-%H")

    snapshots = reset_history.get("snapshots", {})

    def find_snapshot_near(target_key: str) -> dict:
        keys = sorted(snapshots.keys())
        best = None
        for k in keys:
            if k <= target_key:
                best = k
        return snapshots.get(best, {}) if best else {}

    snap_day   = find_snapshot_near(day_key)
    snap_week  = find_snapshot_near(week_key)
    snap_month = find_snapshot_near(month_key)

    # Snapshot mais antigo disponível (para lifetime)
    all_keys   = sorted(snapshots.keys())
    snap_first = snapshots.get(all_keys[0], {}) if all_keys else {}

    total_today    = 0
    total_week     = 0
    total_month    = 0
    total_lifetime = 0
    total_current  = sum(m["resets"] for m in members)
    num_members    = len(members)

    for member in members:
        name    = member["name"]
        current = member["resets"]

        total_today    += max(0, current - snap_day.get(name,   current))
        total_week     += max(0, current - snap_week.get(name,  current))
        total_month    += max(0, current - snap_month.get(name, current))
        total_lifetime += max(0, current - snap_first.get(name, current))

    return {
        "guild":           guild_name,
        "num_members":     num_members,
        "total_current":   total_current,
        "resets_today":    total_today,
        "resets_week":     total_week,
        "resets_month":    total_month,
        "resets_lifetime": total_lifetime,
        "avg_per_player":  round(total_current / num_members, 1) if num_members else 0,
    }


# ── Entradas e saídas de membros ──────────────────────────────────────────────

def update_member_changes(current_members: list, previous_history: dict) -> dict:
    """
    Detecta membros que entraram ou saíram da guilda.
    Mantém log histórico de todas as mudanças.
    Na primeira execução apenas salva a lista sem registrar eventos.
    """
    current_names  = {m["name"] if isinstance(m, dict) else m for m in current_members}
    previous_names = set(previous_history.get("last_member_list", []))
    changes_log    = previous_history.get("changes_log", [])

    # Primeira execução — só salva a lista, não registra eventos
    if not previous_names:
        print(f"[history] primeira execução — salvando lista com {len(current_names)} membros sem registrar eventos")
        return {
            "last_member_list": list(current_names),
            "changes_log":      [],
            "recent_changes":   [],
        }

    joined = current_names - previous_names
    left   = previous_names - current_names

    new_changes = []

    for name in joined:
        event = {
            "name":      name,
            "type":      "joined",
            "timestamp": now_str(),
        }
        new_changes.append(event)
        changes_log.append(event)
        print(f"[history] entrou na guilda inimiga: {name}")

    for name in left:
        event = {
            "name":      name,
            "type":      "left",
            "timestamp": now_str(),
        }
        new_changes.append(event)
        changes_log.append(event)
        print(f"[history] saiu da guilda inimiga: {name}")

    if not new_changes:
        print("[history] nenhuma mudança de membros detectada")

    # Mantém apenas últimos 500 eventos
    changes_log = changes_log[-500:]

    return {
        "last_member_list": list(current_names),
        "changes_log":      changes_log,
        "recent_changes":   new_changes,
    }


# ── Hunted list ───────────────────────────────────────────────────────────────

def update_hunted_list(
    current_enemy_members: list,
    war_deaths: list,
    previous_history: dict,
) -> dict:
    """
    Detecta jogadores suspeitos:
    - Saíram da guilda inimiga mas continuam aparecendo como killers nos deaths
    - Entraram e saíram rapidamente (menos de 48h)
    """
    current_names  = {m["name"] if isinstance(m, dict) else m for m in current_enemy_members}
    previous_names = set(previous_history.get("last_enemy_list", []))
    hunted         = previous_history.get("hunted", {})
    suspect_log    = previous_history.get("suspect_log", [])

    # Detecta quem saiu da guilda inimiga
    left_enemy = previous_names - current_names

    # Killers nos deaths recentes (últimas 48h)
    recent_killers = {d["killedBy"] for d in war_deaths}

    new_suspects = []
    for name in left_enemy:
        if name in recent_killers:
            if name not in hunted:
                event = {
                    "name":      name,
                    "reason":    "Saiu da guilda inimiga mas continuou atacando membros",
                    "timestamp": now_str(),
                }
                hunted[name]   = event
                new_suspects.append(event)
                suspect_log.append(event)
                print(f"[history] suspeito detectado: {name}")

    # Detecta quem entrou e saiu rapidamente (presente no log de mudanças)
    changes_log = previous_history.get("changes_log", [])
    rapid_movers = {}
    for change in changes_log[-100:]:
        name = change["name"]
        if name not in rapid_movers:
            rapid_movers[name] = []
        rapid_movers[name].append(change["type"])

    for name, moves in rapid_movers.items():
        if moves.count("joined") >= 1 and moves.count("left") >= 1:
            if name not in hunted:
                event = {
                    "name":      name,
                    "reason":    "Entrou e saiu da guilda inimiga rapidamente",
                    "timestamp": now_str(),
                }
                hunted[name]   = event
                new_suspects.append(event)
                suspect_log.append(event)
                print(f"[history] suspeito (movimentação rápida): {name}")

    if not new_suspects:
        print("[history] nenhum suspeito novo detectado")

    return {
        "last_enemy_list": list(current_names),
        "hunted":          hunted,
        "suspect_log":     suspect_log,
    }


# ── Carregar/salvar histórico ─────────────────────────────────────────────────

def load_history(path: str) -> dict:
    """Carrega histórico completo do arquivo JSON anterior."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("history", {})
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"[history] nenhum histórico anterior em '{path}', iniciando do zero")
        return {}


# ── Teste direto ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from members import fetch_guild_members

    MY_GUILD    = "Lowly People"
    ENEMY_GUILD = "MentaliTY"

    print("=== Buscando membros ===")
    r_mine  = fetch_guild_members(MY_GUILD)
    r_enemy = fetch_guild_members(ENEMY_GUILD)

    if not r_mine["ok"] or not r_enemy["ok"]:
        print("Erro ao buscar membros")
        exit(1)

    # Carrega histórico anterior
    prev = load_history("history_test.json")

    print("\n=== Atualizando histórico de resets ===")
    reset_hist = update_reset_history(
        r_mine["members"],
        prev.get("resets", {}),
    )

    print("\n=== Calculando resets do dia ===")
    resets_today = compute_resets_today(r_mine["members"], reset_hist)
    print(f"   Membros com reset hoje: {len(resets_today)}")
    for m in resets_today[:5]:
        print(f"   {m['name']:25} hoje={m['resets_today']} semana={m['resets_week']} total={m['resets_total']}")

    print("\n=== Detectando mudanças de membros ===")
    member_changes = update_member_changes(
        r_mine["members"],
        prev.get("members", {}),
    )

    print("\n=== Atualizando hunted list ===")
    # Usa war_test.json se existir para pegar deaths recentes
    try:
        with open("war_test.json", encoding="utf-8") as f:
            war_data = json.load(f)
        recent_deaths = war_data.get("war_history", {}).get("all_deaths", [])
    except FileNotFoundError:
        recent_deaths = []

    hunted_data = update_hunted_list(
        r_enemy["members"],
        recent_deaths,
        prev.get("hunted", {}),
    )

    # Monta e salva histórico completo
    history = {
        "resets":  reset_hist,
        "members": member_changes,
        "hunted":  hunted_data,
    }

    output = {"history": history}
    with open("history_test.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Histórico salvo em history_test.json")
    print(f"   Snapshots de resets: {len(reset_hist['snapshots'])}")
    print(f"   Eventos de reset:    {len(reset_hist['events'])}")
    print(f"   Log de mudanças:     {len(member_changes['changes_log'])}")
    print(f"   Hunted list:         {len(hunted_data['hunted'])}")

    # Roda uma segunda vez para validar que não duplica
    print("\n=== Rodando segunda vez (não deve duplicar) ===")
    prev2        = json.load(open("history_test.json"))["history"]
    reset_hist2  = update_reset_history(r_mine["members"], prev2.get("resets", {}))
    changes2     = update_member_changes(r_mine["members"], prev2.get("members", {}))
    print(f"   Eventos novos de reset: {len([e for e in reset_hist2['events'] if e not in reset_hist['events']])}")
    print(f"   Mudanças novas: {len(changes2['recent_changes'])}")
