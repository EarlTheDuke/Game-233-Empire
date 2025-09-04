"""
Game 233 Empire — a modern, faithful reproduction of the classic 1977 turn-based strategy game "Empire".

Minimal Viable Prototype (MVP):
- Generates a random ASCII world map with land and ocean.
- Places a handful of cities on land.
- Spawns one player unit and reveals fog-of-war around it.
- Renders a scrollable viewport using curses if available; falls back to a basic text input loop otherwise.

Sprints plan:
- Sprint 1 (Map):
  - World generation (islands/continents), city placement (~60 target later), fog-of-war mechanics.
  - Rendering with curses, panning/scrolling, viewport.
- Sprint 2 (Units & Production):
  - Unit class hierarchy (A, D, F, S, TT, C, BB, Nuke), stats and movement points.
  - City production queues and per-turn progress.
- Sprint 3 (Turns & Movement):
  - Turn-based flow, orders/move/edit modes, sentry/waypoints, fuel for aircraft.
- Sprint 4 (Combat):
  - Probabilistic combat resolution, unit matchups, city conquest rules (~50% loop feel), fallout for nukes (later).
- Sprint 5 (AI & Polish):
  - Basic AI for expansion and targeting nearby cities, async save/load (BBS-style), ANSI colors, balance tweaks.

Reference for gameplay details: see the blog post "Game 233: Empire" by Data Driven Gamer (2021).
"""

from __future__ import annotations

import sys
import time
from typing import Dict, List, Optional, Tuple
import os

try:
    import curses  # type: ignore
    HAS_CURSES = True
except Exception:
    curses = None  # type: ignore
    HAS_CURSES = False

from map import GameMap, City
from units import Army, Unit, Fighter, Carrier, NuclearMissile
from player import Player
from combat import resolve_attack
from savegame import save_full_game, load_full_game


Viewport = Tuple[int, int, int, int]  # x, y, width, height

# Visibility radii (can be tuned)
UNIT_SIGHT = 3
CITY_SIGHT = 5


# --- Session statistics (kills/losses by type) ---
GAME_STATS: Dict[str, Dict[str, Dict[str, int]]] = {}
# Simple ring buffer for recent battle reports (latest last)
BATTLE_REPORTS: List[str] = []
MAX_REPORTS: int = 12


def init_game_stats(players: List[str]) -> None:
    global GAME_STATS
    types = ["Army", "Fighter", "Carrier", "NuclearMissile"]
    GAME_STATS = {p: {"kills": {t: 0 for t in types}, "losses": {t: 0 for t in types}} for p in players}


def record_kill(killer_owner: str, victim: Unit) -> None:
    vtype = type(victim).__name__
    if killer_owner in GAME_STATS and vtype in GAME_STATS[killer_owner]["kills"]:
        GAME_STATS[killer_owner]["kills"][vtype] += 1


def record_loss(owner: str, unit: Unit) -> None:
    utype = type(unit).__name__
    if owner in GAME_STATS and utype in GAME_STATS[owner]["losses"]:
        GAME_STATS[owner]["losses"][utype] += 1


def add_battle_report(line: str) -> None:
    BATTLE_REPORTS.append(line)
    if len(BATTLE_REPORTS) > MAX_REPORTS:
        del BATTLE_REPORTS[0:len(BATTLE_REPORTS) - MAX_REPORTS]

def build_stats_lines(active_player: str, units: List[Unit], vw: int, world: GameMap, sidebar_w: int, max_cols: Optional[int] = None) -> List[str]:
    # Only show when full-screen map is visible; caller controls visibility
    # Compute forces for active player
    forces: Dict[str, int] = {"Army": 0, "Fighter": 0, "Carrier": 0, "NuclearMissile": 0}
    for u in units:
        if u.is_alive() and u.owner == active_player:
            nm = type(u).__name__
            if nm in forces:
                forces[nm] += 1
    stats = GAME_STATS.get(active_player, {"kills": {}, "losses": {}})
    kills = stats.get("kills", {})
    losses = stats.get("losses", {})
    def fmt_row(label: str, data: Dict[str, int]) -> str:
        return (
            f" {label}: A {data.get('Army', 0)}"
            f" F {data.get('Fighter', 0)}"
            f" C {data.get('Carrier', 0)}"
            f" M {data.get('NuclearMissile', 0)}"
        )
    base_lines: List[str] = [
        "",
        "Stats:",
        fmt_row("Kills", kills),
        fmt_row("Losses", losses),
        fmt_row("Forces", forces),
    ]
    # Append battle reports under stats
    if BATTLE_REPORTS:
        base_lines.append("")
        base_lines.append("Reports:")
        # show most recent first
        for entry in reversed(BATTLE_REPORTS[-8:]):
            base_lines.append(f" {entry}")
    if max_cols is None or max_cols <= 0:
        return base_lines
    # Wrap lines to available width
    wrapped: List[str] = []
    for line in base_lines:
        s = line
        if len(s) <= max_cols:
            wrapped.append(s)
            continue
        # naive hard wrap
        start = 0
        while start < len(s):
            wrapped.append(s[start:start + max_cols])
            start += max_cols
    return wrapped


def clamp(value: int, low: int, high: int) -> int:
    if value < low:
        return low
    if value > high:
        return high
    return value


def overlay_units_on_buffer(buffer_lines: List[str], view: Viewport, units: List[Unit]) -> List[str]:
    vx, vy, vw, vh = view
    # Convert to mutable 2D char array
    canvas: List[List[str]] = [list(line) for line in buffer_lines]
    for u in units:
        if vx <= u.x < vx + vw and vy <= u.y < vy + vh:
            ux, uy = u.x - vx, u.y - vy
            if 0 <= uy < len(canvas) and 0 <= ux < len(canvas[uy]):
                # Show unit symbol, case indicates owner (P1 uppercase, P2 lowercase)
                ch = u.symbol if u.owner == 'P1' else u.symbol.lower()
                canvas[uy][ux] = ch
    return ["".join(row) for row in canvas]


def build_initial_game(width: int = 60, height: int = 24) -> Tuple[GameMap, Player, Player, List[Unit]]:
    world = GameMap(width, height)
    world.generate(seed=None)
    world.place_cities(count=12, min_separation=3)

    p1 = Player(name="P1", is_ai=False)
    p2 = Player(name="P2", is_ai=False)

    # Assign starter cities if available
    if len(world.cities) >= 2:
        world.cities[0].owner = p1.name
        p1.cities.add((world.cities[0].x, world.cities[0].y))
        world.cities[-1].owner = p2.name
        p2.cities.add((world.cities[-1].x, world.cities[-1].y))

    # Spawn one army for each player at their city or nearest land tile
    p1_x, p1_y = world.find_spawn_for_player(p1.name)
    p2_x, p2_y = world.find_spawn_for_player(p2.name)
    units: List[Unit] = [
        Army(x=p1_x, y=p1_y, owner=p1.name),
        Army(x=p2_x, y=p2_y, owner=p2.name),
    ]
    for u in units:
        u.reset_moves()
    # Assign home city for starter armies so support caps count them
    for u in units:
        for c in world.cities:
            if c.owner == u.owner:
                u.home_city = (c.x, c.y)
                break

    # Per-player FoW will be initialized in the UI entry point

    # Initialize default production values for owned cities (optional)
    for c in world.cities:
        if c.owner in (p1.name, p2.name):
            c.production_type = "Army"
            c.production_cost = 12
            c.production_progress = 0

    # Initialize session stats for both players
    init_game_stats([p1.name, p2.name])
    return world, p1, p2, units


def render_view(world: GameMap, view: Viewport, units: List[Unit], active_player: Optional[str] = None) -> List[str]:
    base = world.render(view, active_player=active_player)
    if active_player is None:
        return overlay_units_on_buffer(base, view, units)
    # Filter enemy units by visibility
    vis = world.visible.get(active_player)
    filtered: List[Unit] = []
    for u in units:
        if not u.is_alive():
            continue
        if u.owner == active_player:
            filtered.append(u)
        else:
            if vis is not None and 0 <= u.y < world.height and 0 <= u.x < world.width and vis[u.y][u.x]:
                filtered.append(u)
    return overlay_units_on_buffer(base, view, filtered)


# --- Game helpers for hot-seat ---
def get_units_for_owner(units: List[Unit], owner: str) -> List[Unit]:
    return [u for u in units if u.owner == owner and u.is_alive()]


def unit_at(units: List[Unit], x: int, y: int) -> Optional[Unit]:
    for u in units:
        if u.is_alive() and u.x == x and u.y == y:
            return u
    return None


def city_at(world: GameMap, x: int, y: int) -> Optional[City]:
    for c in world.cities:
        if c.x == x and c.y == y:
            return c
    return None


def is_land(world: GameMap, x: int, y: int) -> bool:
    if not (0 <= x < world.width and 0 <= y < world.height):
        return False
    return world.tiles[y][x] == '+'


def can_found_city(world: GameMap, units: List[Unit], u: Unit) -> bool:
    if not isinstance(u, Army):
        return False
    if not u.is_alive():
        return False
    # Must be on land, no existing city here, and tile not occupied by enemy unit
    if not is_land(world, u.x, u.y):
        return False
    if city_at(world, u.x, u.y) is not None:
        return False
    # No enemy unit stacked here (shouldn't happen) but be safe
    for other in units:
        if other is not u and other.is_alive() and other.x == u.x and other.y == u.y and other.owner != u.owner:
            return False
    return True


def found_city_from_army(world: GameMap, units: List[Unit], u: Unit) -> bool:
    """Turn the given Army into a city owned by its player. The army is removed."""
    if not can_found_city(world, units, u):
        return False
    # Create city with default production set to Army
    new_city = City(x=u.x, y=u.y, owner=u.owner, production_type='Army', production_progress=0, production_cost=8)
    world.cities.append(new_city)
    # Mark on player's city set if tracked
    # Player sets are updated elsewhere in flow; for now we keep map as source of truth
    # Remove (kill) the unit
    u.hp = 0
    return True


def try_capture_city(world: GameMap, unit: Unit) -> bool:
    c = city_at(world, unit.x, unit.y)
    if c is None:
        return False
    # Air units cannot capture cities
    if isinstance(unit, Fighter):
        return False
    if c.owner != unit.owner:
        c.owner = unit.owner
        # auto-set basic production on capture
        c.production_type = 'Army'
        c.production_cost = 8
        c.production_progress = 0
        return True
    return False


def try_move_unit(world: GameMap, units: List[Unit], u: Unit, dx: int, dy: int) -> Tuple[bool, bool, bool, str]:
    """
    Returns (moved_or_fought, captured_city, immediate_victory, message)
    """
    if not u.can_move():
        return False, False, False, ""
    nx, ny = u.x + dx, u.y + dy
    # Prevent moving off-map: if target is out of bounds, ignore
    if not (0 <= nx < world.width and 0 <= ny < world.height):
        return False, False, False, ""
    # Terrain constraint: ground units must stay on land; fighters and missiles can fly over any tile
    if not isinstance(u, Fighter) and not isinstance(u, Carrier) and not isinstance(u, NuclearMissile) and not is_land(world, nx, ny):
        return False, False, False, ""
    # Carriers must stay on ocean
    if isinstance(u, Carrier):
        if 0 <= nx < world.width and 0 <= ny < world.height and world.tiles[ny][nx] != '.':
            return False, False, False, ""
    # Special handling for NuclearMissile straight-line constraint and skipping over blockers
    if isinstance(u, NuclearMissile):
        # Set direction on first move
        if u.direction_dx is None and u.direction_dy is None:
            if dx == 0 and dy == 0:
                return False, False, False, ""
            u.direction_dx, u.direction_dy = dx, dy
        else:
            if dx != u.direction_dx or dy != u.direction_dy:
                return False, False, False, "Missile locked to direction"
        # Terrain: missiles can fly over any terrain
        # If tile occupied, try to hop one extra tile in same direction (costs 2 moves, counts 2 range)
        blocking_next = unit_at(units, nx, ny)
        if blocking_next is not None:
            if u.moves_left < 2:
                return False, False, False, "Missile hop needs 2 moves"
            nx2, ny2 = nx + dx, ny + dy
            if not (0 <= nx2 < world.width and 0 <= ny2 < world.height):
                return False, False, False, "Missile hop out of bounds"
            if unit_at(units, nx2, ny2) is not None:
                return False, False, False, "Missile hop landing occupied"
            # Perform hop
            u.x, u.y = nx2, ny2
            u.moves_left -= 2
            u.traveled += 2
        else:
            # Normal step
            u.x, u.y = nx, ny
            u.moves_left -= 1
            u.traveled += 1
        # Auto-detonate at max distance
        if u.traveled >= 40 or u.moves_left <= 0:
            det_u, det_c = detonate_missile(world, units, u.owner, u.x, u.y, radius=10)
            u.hp = 0
            return True, False, False, f"Missile detonated: units {det_u}, cities {det_c}"
        return True, False, False, ""

    blocking = unit_at(units, nx, ny)
    if blocking is not None:
        if blocking.owner == u.owner:
            # Skip-over rule: Fighters may hop over a friendly unit if they have 2+ moves
            if isinstance(u, Fighter):
                if u.moves_left >= 2:
                    nx2, ny2 = nx + dx, ny + dy
                    if 0 <= nx2 < world.width and 0 <= ny2 < world.height:
                        if unit_at(units, nx2, ny2) is None:
                            # Fighters can land on any terrain; perform hop
                            u.x, u.y = nx2, ny2
                            u.moves_left -= 2
                            # Fighters cannot capture; call anyway for consistency (will no-op)
                            _ = try_capture_city(world, u)
                            return True, False, False, "Hopped over friendly"
                        else:
                            return False, False, False, "Hop blocked: landing occupied"
                    else:
                        return False, False, False, "Hop blocked: out of bounds"
                else:
                    return False, False, False, "Need 2 moves to hop over friendly"
            return False, False, False, ""  # cannot stack for others
        # combat (Carriers have no special attack; use default odds)
        # Missiles fly over any units; they do not engage in combat
        if isinstance(u, NuclearMissile):
            # Allow passing over by treating as if no blocker (handled in missile branch above normally)
            pass
        # Base odds; special-case Fighter vs Army per balance tweaks
        if isinstance(u, Fighter) and isinstance(blocking, Army):
            a_hit = 0.53
            d_hit = 0.47
        elif isinstance(u, Fighter):
            a_hit = 0.60
            d_hit = 0.40
        else:
            a_hit = 0.53
            d_hit = 0.52
        # City defense bonus increased to ±0.15
        cdef = city_at(world, nx, ny)
        if cdef is not None and cdef.owner == blocking.owner:
            a_hit -= 0.15
            d_hit += 0.15
        attacker_alive, defender_alive = resolve_attack(u, blocking, attacker_hit=a_hit, defender_hit=d_hit)
        # Add concise battle report
        loc = f"@({nx},{ny})"
        atk = f"{u.owner} {type(u).__name__}"
        dfd = f"{blocking.owner} {type(blocking).__name__}"
        city_tag = " city" if (cdef is not None and cdef.owner == blocking.owner) else ""
        outcome = "kill" if not defender_alive else ("trade" if not attacker_alive else "clash")
        add_battle_report(f"{atk} vs {dfd}{city_tag} {loc} a:{a_hit:.2f} d:{d_hit:.2f} -> {outcome}")
        if not defender_alive:
            # remove defender; move in if attacker alive
            blocking.hp = 0
            # record kill/loss
            record_kill(u.owner, blocking)
            record_loss(blocking.owner, blocking)
            if attacker_alive:
                u.x, u.y = nx, ny
                u.moves_left -= 1
                captured = try_capture_city(world, u)
                if captured:
                    opponent = 'P2' if u.owner == 'P1' else 'P1'
                    opp_city_count = sum(1 for c in world.cities if c.owner == opponent)
                    my_city_count = sum(1 for c in world.cities if c.owner == u.owner)
                    return True, True, (opp_city_count == 0 and my_city_count > 0), "Defender destroyed"
                return True, False, False, "Defender destroyed"
        else:
            # defender survived; attacker may have died
            if not attacker_alive:
                u.hp = 0
                record_loss(u.owner, u)
                record_kill(blocking.owner, u)
        return True, False, False, "Attacker destroyed"
    # Move into empty tile
    u.x, u.y = nx, ny
    u.moves_left -= 1
    captured = try_capture_city(world, u)
    if captured:
        opponent = 'P2' if u.owner == 'P1' else 'P1'
        opp_city_count = sum(1 for c in world.cities if c.owner == opponent)
        my_city_count = sum(1 for c in world.cities if c.owner == u.owner)
        return True, True, (opp_city_count == 0 and my_city_count > 0), "City captured"
    return True, False, False, ""


# --- Production system (extensible) ---
from typing import Any, Callable


def is_tile_free(world: GameMap, units: List[Unit], x: int, y: int) -> bool:
    return unit_at(units, x, y) is None


def spawn_army(world: GameMap, units: List[Unit], city: City) -> bool:
    spawn_positions: List[Tuple[int, int]] = [
        (city.x, city.y),
        (city.x + 1, city.y), (city.x - 1, city.y), (city.x, city.y + 1), (city.x, city.y - 1),
        (city.x + 1, city.y + 1), (city.x - 1, city.y - 1), (city.x + 1, city.y - 1), (city.x - 1, city.y + 1),
    ]
    # Enforce support cap for Armies only
    def is_supported_by_city(u: Unit, c: City) -> bool:
        return isinstance(u, Army) and u.owner == c.owner and u.home_city == (c.x, c.y)
    supported = sum(1 for u in units if is_supported_by_city(u, city) and u.is_alive())
    if supported >= city.support_cap:
        return False
    for (sx, sy) in spawn_positions:
        if 0 <= sx < world.width and 0 <= sy < world.height:
            if is_land(world, sx, sy) and is_tile_free(world, units, sx, sy):
                nu = Army(x=sx, y=sy, owner=city.owner or "")
                nu.reset_moves()
                nu.home_city = (city.x, city.y)
                units.append(nu)
                return True
    return False


def spawn_fighter(world: GameMap, units: List[Unit], city: City) -> bool:
    # Fighters spawn only on the city tile if free
    sx, sy = city.x, city.y
    if 0 <= sx < world.width and 0 <= sy < world.height:
        if is_tile_free(world, units, sx, sy):
            nu = Fighter(x=sx, y=sy, owner=city.owner or "")
            nu.reset_moves()
            nu.home_city = (city.x, city.y)
            units.append(nu)
            return True
    return False


def spawn_missile(world: GameMap, units: List[Unit], city: City) -> bool:
    # Spawn only on city tile if free
    sx, sy = city.x, city.y
    if 0 <= sx < world.width and 0 <= sy < world.height:
        if is_tile_free(world, units, sx, sy):
            nu = NuclearMissile(x=sx, y=sy, owner=city.owner or "")
            nu.reset_moves()
            nu.home_city = (city.x, city.y)
            units.append(nu)
            return True
    return False

def spawn_carrier(world: GameMap, units: List[Unit], city: City) -> bool:
    # Place on any adjacent ocean tile (not diagonal preferred first)
    candidates: List[Tuple[int, int]] = [
        (city.x + 1, city.y), (city.x - 1, city.y), (city.x, city.y + 1), (city.x, city.y - 1),
        (city.x + 1, city.y + 1), (city.x - 1, city.y - 1), (city.x + 1, city.y - 1), (city.x - 1, city.y + 1),
    ]
    for sx, sy in candidates:
        if 0 <= sx < world.width and 0 <= sy < world.height:
            if world.tiles[sy][sx] == '.' and is_tile_free(world, units, sx, sy):
                nu = Carrier(x=sx, y=sy, owner=city.owner or "")
                nu.reset_moves()
                nu.home_city = (city.x, city.y)
                units.append(nu)
                return True
    return False


def detonate_missile(world: GameMap, units: List[Unit], owner: str, x: int, y: int, radius: int = 20) -> Tuple[int, int]:
    r2 = radius * radius
    # Destroy units in radius and neutralize cities
    units_killed = 0
    for v in units:
        if not v.is_alive():
            continue
        dx = v.x - x
        dy = v.y - y
        if dx * dx + dy * dy <= r2:
            if v.owner == owner:
                record_loss(owner, v)
            else:
                record_kill(owner, v)
                record_loss(v.owner, v)
            v.hp = 0
            units_killed += 1
    cities_neutralized = 0
    for c in world.cities:
        dx = c.x - x
        dy = c.y - y
        if dx * dx + dy * dy <= r2:
            if c.owner is not None:
                c.owner = None
                cities_neutralized += 1
            # Keep production settings; ownership neutralized only
    return units_killed, cities_neutralized

PRODUCTION_CATALOG: Dict[str, Dict[str, Any]] = {
    "Army": {"cost": 12, "spawn": spawn_army, "label": "Army"},
    "Fighter": {"cost": 20, "spawn": spawn_fighter, "label": "Fighter"},
    "Carrier": {"cost": 32, "spawn": spawn_carrier, "label": "Carrier"},
    "NuclearMissile": {"cost": 75, "spawn": spawn_missile, "label": "Nuke"},
}


def set_city_production(city: City, prod_type: str) -> bool:
    if prod_type not in PRODUCTION_CATALOG:
        return False
    city.production_type = prod_type
    city.production_cost = int(PRODUCTION_CATALOG[prod_type].get("cost", 0))
    return True


def cycle_city_production(city: City) -> None:
    options = list(PRODUCTION_CATALOG.keys())
    if not options:
        return
    try:
        idx = options.index(city.production_type)  # type: ignore[arg-type]
    except Exception:
        idx = -1
    next_type = options[(idx + 1) % len(options)]
    set_city_production(city, next_type)


def enforce_fighter_basing(world: GameMap, units: List[Unit], owner: str) -> None:
    # Fighters must end turn on a friendly city tile or are destroyed
    for u in units:
        if not u.is_alive():
            continue
        if isinstance(u, Fighter) and u.owner == owner:
            c = city_at(world, u.x, u.y)
            if c is not None and c.owner == owner:
                continue
            # Allow adjacency to friendly Carrier (orthogonal)
            adjacent_ok = False
            for dx, dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(-1,-1),(1,-1),(-1,1)):
                ax, ay = u.x + dx, u.y + dy
                v = unit_at(units, ax, ay)
                if v is not None and v.is_alive() and isinstance(v, Carrier) and v.owner == owner:
                    adjacent_ok = True
                    break
            if not adjacent_ok:
                u.hp = 0


def advance_production_and_spawn(world: GameMap, units: List[Unit]) -> None:
    for c in world.cities:
        if c.owner is None or c.owner == 'neutral':
            continue
        if not c.production_type or c.production_cost <= 0:
            continue
        c.production_progress += 1
        target = c.production_type
        entry = PRODUCTION_CATALOG.get(target)
        if entry is None:
            continue
        if c.production_progress >= entry.get("cost", c.production_cost):
            placed = False
            spawn_fn = entry.get("spawn")
            if callable(spawn_fn):
                placed = bool(spawn_fn(world, units, c))  # type: ignore[misc]
            if placed:
                c.production_progress = 0
            else:
                c.production_progress = c.production_cost

    # Healing: +1 hp/turn in owned cities (cap at max_hp)
    for u in units:
        if not u.is_alive():
            continue
        c = city_at(world, u.x, u.y)
        if c is not None and c.owner == u.owner and u.hp < u.max_hp:
            u.hp = min(u.max_hp, u.hp + 1)


def reset_moves_for_owner(units: List[Unit], owner: str) -> None:
    for u in units:
        if u.owner == owner and u.is_alive():
            u.reset_moves()


def select_next_unit(units: List[Unit], owner: str, current: Optional[Unit]) -> Optional[Unit]:
    own_units = [u for u in units if u.owner == owner and u.is_alive()]
    if not own_units:
        return None
    if current is None or current not in own_units:
        # find first with moves
        for u in own_units:
            if u.can_move():
                return u
        return own_units[0]
    # rotate
    idx = own_units.index(current)
    for i in range(1, len(own_units) + 1):
        cand = own_units[(idx + i) % len(own_units)]
        if cand.can_move():
            return cand
    return own_units[(idx + 1) % len(own_units)]


def select_next_unit_any(units: List[Unit], owner: str, current: Optional[Unit]) -> Optional[Unit]:
    own_units = [u for u in units if u.owner == owner and u.is_alive()]
    if not own_units:
        return None
    if current is None or current not in own_units:
        return own_units[0]
    idx = own_units.index(current)
    return own_units[(idx + 1) % len(own_units)]


def recompute_visibility(world: GameMap, owner: str, units: List[Unit]) -> None:
    # Clear and mark for the owner based on cities and units
    world.clear_visible_for(owner)
    for c in world.cities:
        if c.owner == owner:
            world.mark_visible_circle(owner, c.x, c.y, radius=CITY_SIGHT)
    for u in units:
        if u.owner == owner and u.is_alive():
            # Fighters provide extended sight
            radius = 5 if isinstance(u, Fighter) else UNIT_SIGHT
            world.mark_visible_circle(owner, u.x, u.y, radius=radius)


def build_sidebar_lines(ui: str) -> List[str]:
    # ui: 'curses' or 'fallback' for key differences
    if ui == 'curses':
        commands = [
            "Commands:",
            " N  Next unit",
            " Arrows/Numpad 8/2/4/6 Move (Numpad diagonals)",
            " B  Set Army",
            " R  Set Fighter",
            " P  Cycle Production",
            " F  Found City",
            " C  Cycle Cities",
            " S  Save, O Load",
            " Space End turn",
            " Q  Quit",
            " D  Detonate Nuclear Missile",
        ]
    else:
        commands = [
            "Commands:",
            " n  Next unit",
            " i/j/k/l Move",
            " b  Set Army",
            " r  Set Fighter",
            " p  Cycle Production",
            " f  Found City",
            " c  Cycle Cities",
            " e  End turn",
            " q  Quit",
            " Pan: w/a/s/d",
        ]
    terrain = [
        "",
        "Terrain:",
        " +  Land",
        " .  Ocean",
        " O  City (P1)",
        " X  City (P2)",
        " o  City (Neutral)",
    ]
    units_help = [
        "",
        "Units:",
        " A  Army (P1)",
        " a  Army (P2)",
        " F  Fighter (P1)",
        " f  Fighter (P2)",
        " C  Carrier (P1)",
        " c  Carrier (P2)",
        " M  Nuclear Missile (P1)",
        " m  Nuclear Missile (P2)",
    ]
    return commands + terrain + units_help


def center_view_on(world: GameMap, vw: int, vh: int, target_x: int, target_y: int) -> Tuple[int, int]:
    vx = clamp(target_x - vw // 2, 0, max(0, world.width - vw))
    vy = clamp(target_y - vh // 2, 0, max(0, world.height - vh))
    return vx, vy


def ensure_save_dir() -> str:
    save_dir = os.path.join(os.getcwd(), "saved games")
    os.makedirs(save_dir, exist_ok=True)
    return save_dir

def run_curses(world: GameMap, p1: Player, p2: Player, units: List[Unit]) -> None:
    assert HAS_CURSES and curses is not None

    def _main(stdscr: "curses._CursesWindow") -> None:  # type: ignore
        curses.curs_set(0)
        stdscr.nodelay(False)
        stdscr.keypad(True)

        max_y, max_x = stdscr.getmaxyx()
        # Reserve one line for status
        vw = max(20, min(world.width, max_x))
        vh = max(10, min(world.height, max_y - 1))
        vx, vy = 0, 0

        current_player = p1.name
        turn_number = 1
        selected: Optional[Unit] = None
        focused_city_index: Optional[int] = None
        # reserve sidebar width
        sidebar_lines = build_sidebar_lines('curses')
        sidebar_w = max(len(line) for line in sidebar_lines) + 1
        vw = max(20, min(world.width, max_x - sidebar_w))
        vh = max(10, min(world.height, max_y - 1))
        vx, vy = 0, 0
        reset_moves_for_owner(units, current_player)
        # Initialize per-player FoW
        world.init_fow([p1.name, p2.name])
        # Seed exploration around all owned cities and units for both players
        for c in world.cities:
            if c.owner in (p1.name, p2.name):
                world.mark_visible_circle(c.owner, c.x, c.y, radius=CITY_SIGHT)
        for u in units:
            world.mark_visible_circle(u.owner, u.x, u.y, radius=UNIT_SIGHT)
        # Recompute for current player specifically
        recompute_visibility(world, current_player, units)
        # Center on first ready unit for current player
        selected = select_next_unit(units, current_player, None)
        if selected is not None:
            vx, vy = center_view_on(world, vw, vh, selected.x, selected.y)

        while True:
            # Dynamically adapt viewport size to current terminal window
            try:
                max_y, max_x = stdscr.getmaxyx()
            except Exception:
                max_y, max_x = vh + 1, vw
            new_vw = max(20, min(world.width, max_x - sidebar_w))
            new_vh = max(10, min(world.height, max_y - 1))
            if new_vw != vw or new_vh != vh:
                vw, vh = new_vw, new_vh
                vx = clamp(vx, 0, max(0, world.width - vw))
                vy = clamp(vy, 0, max(0, world.height - vh))
            view: Viewport = (vx, vy, vw, vh)
            lines = render_view(world, view, units, active_player=current_player)
            # Precompute stats panel lines if full map fits
            show_full_map = (vw >= world.width and vh >= world.height)
            stats_lines: List[str] = []
            if show_full_map:
                avail = max(0, max_x - (vw + 1 + sidebar_w) - 1)
                stats_lines = build_stats_lines(current_player, units, vw, world, sidebar_w, max_cols=avail)
            stdscr.erase()
            for row_idx, line in enumerate(lines[:vh]):
                # Draw map line
                stdscr.addstr(row_idx, 0, line[:vw])
                # Highlight selected unit tile (reverse video)
                if selected is not None and selected.is_alive():
                    sx, sy = selected.x - vx, selected.y - vy
                    if sy == row_idx and 0 <= sx < vw:
                        try:
                            stdscr.chgat(row_idx, sx, 1, curses.A_REVERSE)
                        except Exception:
                            pass
                # Highlight focused city tile (standout)
                if focused_city_index is not None:
                    own_cities = [c for c in world.cities if c.owner == current_player]
                    if own_cities:
                        fc = own_cities[focused_city_index % len(own_cities)]
                        cx, cy = fc.x - vx, fc.y - vy
                        if cy == row_idx and 0 <= cx < vw:
                            try:
                                stdscr.chgat(row_idx, cx, 1, curses.A_BOLD)
                            except Exception:
                                pass
                # right-side sidebar content
                if row_idx < len(sidebar_lines):
                    stdscr.addstr(row_idx, vw + 1, sidebar_lines[row_idx][:sidebar_w - 1])
                # Draw stats panel to the right of sidebar when full map visible
                if show_full_map and row_idx < len(stats_lines):
                    offset = vw + 1 + sidebar_w
                    if offset < max_x:
                        stdscr.addstr(row_idx, offset, stats_lines[row_idx][:max(0, max_x - offset - 1)])

            city_under_view: Optional[City] = None
            # Status: player, turn, selected unit, hint keys
            sel_txt = "none"
            if selected is not None and selected.is_alive():
                sel_txt = f"{selected.owner} A @({selected.x},{selected.y}) hp:{selected.hp}/{selected.max_hp} mp:{selected.moves_left}"
            # City info under selected unit
            city_info = ""
            if selected is not None and selected.is_alive():
                c = city_at(world, selected.x, selected.y)
                if c is not None and c.owner == current_player and c.production_type and c.production_cost > 0:
                    eta = max(0, c.production_cost - c.production_progress)
                    city_info = f" | City: {c.production_type} ETA {eta}"
            # Focused city info
            focus_info = ""
            if focused_city_index is not None:
                own_cities = [c for c in world.cities if c.owner == current_player]
                if own_cities:
                    fc = own_cities[focused_city_index % len(own_cities)]
                    eta2 = max(0, (fc.production_cost or 0) - (fc.production_progress or 0)) if fc.production_cost else 0
                    ptxt = fc.production_type or "(none)"
                    focus_info = f" | Focus: ({fc.x},{fc.y}) {ptxt} ETA {eta2}"
            status = (
                f"P:{current_player} T:{turn_number} "
                f"| Sel:{sel_txt}{city_info}{focus_info}"
            )
            stdscr.addstr(vh, 0, status[:vw])
            stdscr.refresh()

            key = stdscr.getch()
            # Pre-handle missile detonation on D/d to avoid conflicts with pan key 'D'
            if key in (ord('d'), ord('D')):
                if selected is not None and isinstance(selected, NuclearMissile) and selected.owner == current_player:
                    det_u, det_c = detonate_missile(world, units, current_player, selected.x, selected.y, radius=10)
                    selected.hp = 0
                    selected = None
                    recompute_visibility(world, current_player, units)
                    msg = f"Nuke: destroyed {det_u} units, neutralized {det_c} cities"
                    stdscr.addstr(vh, 0, msg[:vw])
                    stdscr.refresh()
                    selected = select_next_unit(units, current_player, selected)
                    continue
            if key in (ord('q'), ord('Q')):
                # confirm quit if game in progress
                stdscr.addstr(vh, 0, "Quit? (y/N)"[:vw])
                stdscr.refresh()
                k2 = stdscr.getch()
                if k2 in (ord('y'), ord('Y')):
                    break
                else:
                    continue
            elif key in (ord('a'), ord('A')):
                vx = clamp(vx - 1, 0, max(0, world.width - vw))
            elif key in (ord('d'), ord('D')):
                vx = clamp(vx + 1, 0, max(0, world.width - vw))
            elif key in (ord('w'), ord('W')):
                vy = clamp(vy - 1, 0, max(0, world.height - vh))
            elif key in (ord('s'), ord('S')):
                vy = clamp(vy + 1, 0, max(0, world.height - vh))
            elif key in (ord('n'), ord('N')):
                # Cycle through all own units (even if out of moves) so Armies can found cities after moving
                selected = select_next_unit_any(units, current_player, selected)
            elif key == curses.KEY_UP or key == getattr(curses, 'KEY_UP', -999) or key == getattr(curses, 'KEY_A2', -999) or key == getattr(curses, 'KEY_SR', -999) or key == getattr(curses, 'KEY_BTAB', -999) or key == getattr(curses, 'KEY_SUP', -999) or key == getattr(curses, 'KEY_NUMPAD8', -999):
                if selected is None:
                    selected = select_next_unit(units, current_player, selected)
                if selected is not None and selected.owner == current_player:
                    moved, captured, victory, _ = try_move_unit(world, units, selected, 0, -1)
                    if victory:
                        stdscr.addstr(vh, 0, f"{current_player} wins! Press Q to quit."[:vw])
                        stdscr.refresh()
                        while True:
                            k2 = stdscr.getch()
                            if k2 in (ord('q'), ord('Q')):
                                return
                    if moved:
                        # update FoW after move
                        recompute_visibility(world, current_player, units)
                        # keep selection if unit still has moves
                        if not (selected is not None and selected.is_alive() and selected.can_move()):
                            selected = select_next_unit(units, current_player, selected)
            elif key == curses.KEY_DOWN or key == getattr(curses, 'KEY_DOWN', -999) or key == getattr(curses, 'KEY_C2', -999) or key == getattr(curses, 'KEY_SF', -999) or key == getattr(curses, 'KEY_SDOWN', -999) or key == getattr(curses, 'KEY_NUMPAD2', -999):
                if selected is None:
                    selected = select_next_unit(units, current_player, selected)
                if selected is not None and selected.owner == current_player:
                    moved, captured, victory, _ = try_move_unit(world, units, selected, 0, 1)
                    if victory:
                        stdscr.addstr(vh, 0, f"{current_player} wins! Press Q to quit."[:vw])
                        stdscr.refresh()
                        while True:
                            k2 = stdscr.getch()
                            if k2 in (ord('q'), ord('Q')):
                                return
                    if moved:
                        recompute_visibility(world, current_player, units)
                        if not (selected is not None and selected.is_alive() and selected.can_move()):
                            selected = select_next_unit(units, current_player, selected)
            elif key == curses.KEY_LEFT or key == getattr(curses, 'KEY_LEFT', -999) or key == getattr(curses, 'KEY_B1', -999) or key == getattr(curses, 'KEY_SLEFT', -999) or key == getattr(curses, 'KEY_NUMPAD4', -999):
                if selected is None:
                    selected = select_next_unit(units, current_player, selected)
                if selected is not None and selected.owner == current_player:
                    moved, captured, victory, _ = try_move_unit(world, units, selected, -1, 0)
                    if victory:
                        stdscr.addstr(vh, 0, f"{current_player} wins! Press Q to quit."[:vw])
                        stdscr.refresh()
                        while True:
                            k2 = stdscr.getch()
                            if k2 in (ord('q'), ord('Q')):
                                return
                    if moved:
                        recompute_visibility(world, current_player, units)
                        if not (selected is not None and selected.is_alive() and selected.can_move()):
                            selected = select_next_unit(units, current_player, selected)
            elif key == curses.KEY_RIGHT or key == getattr(curses, 'KEY_RIGHT', -999) or key == getattr(curses, 'KEY_B3', -999) or key == getattr(curses, 'KEY_SRIGHT', -999) or key == getattr(curses, 'KEY_NUMPAD6', -999):
                if selected is None:
                    selected = select_next_unit(units, current_player, selected)
                if selected is not None and selected.owner == current_player:
                    moved, captured, victory, _ = try_move_unit(world, units, selected, 1, 0)
                    if victory:
                        stdscr.addstr(vh, 0, f"{current_player} wins! Press Q to quit."[:vw])
                        stdscr.refresh()
                        while True:
                            k2 = stdscr.getch()
                            if k2 in (ord('q'), ord('Q')):
                                return
                    if moved:
                        recompute_visibility(world, current_player, units)
                        if not (selected is not None and selected.is_alive() and selected.can_move()):
                            selected = select_next_unit(units, current_player, selected)
            # Number keys 8/2/4/6 for movement (NumLock on)
            elif key in (ord('8'),):
                if selected is None:
                    selected = select_next_unit(units, current_player, selected)
                if selected is not None and selected.owner == current_player:
                    moved, captured, victory, _ = try_move_unit(world, units, selected, 0, -1)
                    if victory:
                        stdscr.addstr(vh, 0, f"{current_player} wins! Press Q to quit."[:vw])
                        stdscr.refresh()
                        while True:
                            k2 = stdscr.getch()
                            if k2 in (ord('q'), ord('Q')):
                                return
                    if moved:
                        recompute_visibility(world, current_player, units)
                        if not (selected is not None and selected.is_alive() and selected.can_move()):
                            selected = select_next_unit(units, current_player, selected)
            elif key in (ord('2'),):
                if selected is None:
                    selected = select_next_unit(units, current_player, selected)
                if selected is not None and selected.owner == current_player:
                    moved, captured, victory, _ = try_move_unit(world, units, selected, 0, 1)
                    if victory:
                        stdscr.addstr(vh, 0, f"{current_player} wins! Press Q to quit."[:vw])
                        stdscr.refresh()
                        while True:
                            k2 = stdscr.getch()
                            if k2 in (ord('q'), ord('Q')):
                                return
                    if moved:
                        recompute_visibility(world, current_player, units)
                        if not (selected is not None and selected.is_alive() and selected.can_move()):
                            selected = select_next_unit(units, current_player, selected)
            elif key in (ord('4'),):
                if selected is None:
                    selected = select_next_unit(units, current_player, selected)
                if selected is not None and selected.owner == current_player:
                    moved, captured, victory, _ = try_move_unit(world, units, selected, -1, 0)
                    if victory:
                        stdscr.addstr(vh, 0, f"{current_player} wins! Press Q to quit."[:vw])
                        stdscr.refresh()
                        while True:
                            k2 = stdscr.getch()
                            if k2 in (ord('q'), ord('Q')):
                                return
                    if moved:
                        recompute_visibility(world, current_player, units)
                        if not (selected is not None and selected.is_alive() and selected.can_move()):
                            selected = select_next_unit(units, current_player, selected)
            elif key in (ord('6'),):
                if selected is None:
                    selected = select_next_unit(units, current_player, selected)
                if selected is not None and selected.owner == current_player:
                    moved, captured, victory, _ = try_move_unit(world, units, selected, 1, 0)
                    if victory:
                        stdscr.addstr(vh, 0, f"{current_player} wins! Press Q to quit."[:vw])
                        stdscr.refresh()
                        while True:
                            k2 = stdscr.getch()
                            if k2 in (ord('q'), ord('Q')):
                                return
                    if moved:
                        recompute_visibility(world, current_player, units)
                        if not (selected is not None and selected.is_alive() and selected.can_move()):
                            selected = select_next_unit(units, current_player, selected)
            # Numpad diagonals (support common curses keycodes)
            elif key in (getattr(curses, 'KEY_A1', -999), getattr(curses, 'KEY_HOME', -999)):
                if selected is None:
                    selected = select_next_unit(units, current_player, selected)
                if selected is not None and selected.owner == current_player:
                    moved, captured, victory, _ = try_move_unit(world, units, selected, -1, -1)
                    if victory:
                        stdscr.addstr(vh, 0, f"{current_player} wins! Press Q to quit."[:vw])
                        stdscr.refresh()
                        while True:
                            k2 = stdscr.getch()
                            if k2 in (ord('q'), ord('Q')):
                                return
                    if moved:
                        recompute_visibility(world, current_player, units)
                        if not (selected is not None and selected.is_alive() and selected.can_move()):
                            selected = select_next_unit(units, current_player, selected)
            elif key in (getattr(curses, 'KEY_A3', -999), getattr(curses, 'KEY_PPAGE', -999)):
                if selected is None:
                    selected = select_next_unit(units, current_player, selected)
                if selected is not None and selected.owner == current_player:
                    moved, captured, victory, _ = try_move_unit(world, units, selected, 1, -1)
                    if victory:
                        stdscr.addstr(vh, 0, f"{current_player} wins! Press Q to quit."[:vw])
                        stdscr.refresh()
                        while True:
                            k2 = stdscr.getch()
                            if k2 in (ord('q'), ord('Q')):
                                return
                    if moved:
                        recompute_visibility(world, current_player, units)
                        if not (selected is not None and selected.is_alive() and selected.can_move()):
                            selected = select_next_unit(units, current_player, selected)
            elif key in (getattr(curses, 'KEY_C1', -999), getattr(curses, 'KEY_END', -999)):
                if selected is None:
                    selected = select_next_unit(units, current_player, selected)
                if selected is not None and selected.owner == current_player:
                    moved, captured, victory, _ = try_move_unit(world, units, selected, -1, 1)
                    if victory:
                        stdscr.addstr(vh, 0, f"{current_player} wins! Press Q to quit."[:vw])
                        stdscr.refresh()
                        while True:
                            k2 = stdscr.getch()
                            if k2 in (ord('q'), ord('Q')):
                                return
                    if moved:
                        recompute_visibility(world, current_player, units)
                        if not (selected is not None and selected.is_alive() and selected.can_move()):
                            selected = select_next_unit(units, current_player, selected)
            elif key in (getattr(curses, 'KEY_C3', -999), getattr(curses, 'KEY_NPAGE', -999)):
                if selected is None:
                    selected = select_next_unit(units, current_player, selected)
                if selected is not None and selected.owner == current_player:
                    moved, captured, victory, _ = try_move_unit(world, units, selected, 1, 1)
                    if victory:
                        stdscr.addstr(vh, 0, f"{current_player} wins! Press Q to quit."[:vw])
                        stdscr.refresh()
                        while True:
                            k2 = stdscr.getch()
                            if k2 in (ord('q'), ord('Q')):
                                return
                    if moved:
                        recompute_visibility(world, current_player, units)
                        if not (selected is not None and selected.is_alive() and selected.can_move()):
                            selected = select_next_unit(units, current_player, selected)
                    if victory:
                        stdscr.addstr(vh, 0, f"{current_player} wins! Press Q to quit."[:vw])
                        stdscr.refresh()
                        while True:
                            k2 = stdscr.getch()
                            if k2 in (ord('q'), ord('Q')):
                                return
                    if moved:
                        recompute_visibility(world, current_player, units)
                        if not (selected is not None and selected.is_alive() and selected.can_move()):
                            selected = select_next_unit(units, current_player, selected)
            # Space now ends turn (see below)
            elif key in (ord('b'), ord('B')):
                # Set production at focused or hovered city
                target_city: Optional[City] = None
                if focused_city_index is not None:
                    own_cities = [c for c in world.cities if c.owner == current_player]
                    if own_cities:
                        target_city = own_cities[focused_city_index % len(own_cities)]
                if target_city is None and selected is not None and selected.owner == current_player:
                    target_city = city_at(world, selected.x, selected.y)
                if target_city is not None and target_city.owner == current_player:
                    set_city_production(target_city, 'Army')
            elif key in (ord('f'), ord('F')):
                # Found city from selected army, if valid
                if selected is not None and selected.owner == current_player:
                    if found_city_from_army(world, units, selected):
                        # Update visibility for current player due to new city sight
                        recompute_visibility(world, current_player, units)
                        # Auto-select next unit since this one is gone
                        selected = select_next_unit(units, current_player, None)
            elif key in (ord('r'), ord('R')):
                target_city = None
                if focused_city_index is not None:
                    own_cities = [c for c in world.cities if c.owner == current_player]
                    if own_cities:
                        target_city = own_cities[focused_city_index % len(own_cities)]
                if target_city is None and selected is not None and selected.owner == current_player:
                    target_city = city_at(world, selected.x, selected.y)
                if target_city is not None and target_city.owner == current_player:
                    set_city_production(target_city, 'Fighter')
            elif key in (ord('p'), ord('P')):
                target_city = None
                if focused_city_index is not None:
                    own_cities = [c for c in world.cities if c.owner == current_player]
                    if own_cities:
                        target_city = own_cities[focused_city_index % len(own_cities)]
                if target_city is None and selected is not None and selected.owner == current_player:
                    target_city = city_at(world, selected.x, selected.y)
                if target_city is not None and target_city.owner == current_player:
                    cycle_city_production(target_city)
            elif key in (ord('d'), ord('D')):
                # Detonate missile at current position (if selected is a missile)
                if selected is not None and isinstance(selected, NuclearMissile) and selected.owner == current_player:
                    det_u, det_c = detonate_missile(world, units, current_player, selected.x, selected.y, radius=10)
                    selected.hp = 0
                    # Remove dead missiles immediately from selection
                    selected = None
                    recompute_visibility(world, current_player, units)
                    # Show summary on status line
                    msg = f"Nuke: destroyed {det_u} units, neutralized {det_c} cities"
                    stdscr.addstr(vh, 0, msg[:vw])
                    stdscr.refresh()
                    selected = select_next_unit(units, current_player, selected)
            elif key in (ord('c'), ord('C')):
                own_cities = [c for c in world.cities if c.owner == current_player]
                if own_cities:
                    focused_city_index = 0 if focused_city_index is None else (focused_city_index + 1) % len(own_cities)
                    fc = own_cities[focused_city_index]
                    vx, vy = center_view_on(world, vw, vh, fc.x, fc.y)
            elif key in (ord('s'), ord('S')):
                # Save game prompt
                prompt = "Save as (no extension): "
                stdscr.move(vh, 0)
                stdscr.clrtoeol()
                stdscr.addstr(vh, 0, prompt[:vw])
                stdscr.refresh()
                curses.echo()
                try:
                    curses.curs_set(1)
                except Exception:
                    pass
                maxlen = max(1, min(50, vw - len(prompt) - 1))
                try:
                    name = stdscr.getstr(vh, min(vw - 1, len(prompt)), maxlen).decode('utf-8').strip()
                except Exception:
                    name = ""
                try:
                    curses.curs_set(0)
                except Exception:
                    pass
                curses.noecho()
                if name:
                    save_dir = ensure_save_dir()
                    path = os.path.join(save_dir, f"{name}.json")
                    players_data = [
                        {"name": p1.name, "is_ai": p1.is_ai, "cities": list(p1.cities)},
                        {"name": p2.name, "is_ai": p2.is_ai, "cities": list(p2.cities)},
                    ]
                    save_full_game(path, world, units, players_data, turn_number, current_player)
                    stdscr.move(vh, 0)
                    stdscr.clrtoeol()
                    stdscr.addstr(vh, 0, f"Saved to {path}"[:vw])
                    stdscr.refresh()
            elif key in (ord('o'), ord('O')):
                prompt = "Load name (no extension): "
                stdscr.move(vh, 0)
                stdscr.clrtoeol()
                stdscr.addstr(vh, 0, prompt[:vw])
                # List available saves one line above
                try:
                    list_y = max(0, vh - 1)
                    save_dir = ensure_save_dir()
                    saves = [fn[:-5] for fn in os.listdir(save_dir) if fn.lower().endswith('.json')]
                    list_line = "Saves: " + (" ".join(sorted(saves)) if saves else "(none)")
                    stdscr.addstr(list_y, 0, list_line[:vw])
                except Exception:
                    pass
                stdscr.refresh()
                curses.echo()
                try:
                    curses.curs_set(1)
                except Exception:
                    pass
                maxlen = max(1, min(50, vw - len(prompt) - 1))
                try:
                    name = stdscr.getstr(vh, min(vw - 1, len(prompt)), maxlen).decode('utf-8').strip()
                except Exception:
                    name = ""
                try:
                    curses.curs_set(0)
                except Exception:
                    pass
                curses.noecho()
                if name:
                    save_dir = ensure_save_dir()
                    path = os.path.join(save_dir, f"{name}.json")
                    data = load_full_game(path)
                    # Rehydrate map, units, players, turn
                    loaded_map = data["map"]
                    world.width = loaded_map["width"]
                    world.height = loaded_map["height"]
                    world.tiles = loaded_map["tiles"]
                    world.fog = loaded_map["fog"]
                    world.cities = [City(**c) for c in loaded_map["cities"]]
                    world.explored = loaded_map.get("explored", {})
                    # Units
                    units.clear()
                    for ud in data["units"]:
                        utype = ud.get("unit_type", "Army")
                        if utype == "Fighter":
                            u = Fighter(x=ud["x"], y=ud["y"], owner=ud["owner"]) 
                        elif utype == "Carrier":
                            u = Carrier(x=ud["x"], y=ud["y"], owner=ud["owner"]) 
                        elif utype == "NuclearMissile":
                            u = NuclearMissile(x=ud["x"], y=ud["y"], owner=ud["owner"]) 
                        else:
                            u = Army(x=ud["x"], y=ud["y"], owner=ud["owner"]) 
                        u.symbol = ud.get("symbol", u.symbol)
                        u.max_hp = ud.get("max_hp", u.max_hp)
                        u.hp = ud.get("hp", u.hp)
                        u.movement_points = ud.get("movement_points", u.movement_points)
                        u.fuel = ud.get("fuel", None)
                        u.moves_left = ud.get("moves_left", u.movement_points)
                        u.home_city = tuple(ud["home_city"]) if ud.get("home_city") else None
                        units.append(u)
                    # Players
                    pdat = data.get("players", [])
                    if len(pdat) >= 2:
                        p1.name = pdat[0].get("name", p1.name)
                        p1.is_ai = pdat[0].get("is_ai", False)
                        p1.cities = set(tuple(t) for t in pdat[0].get("cities", list(p1.cities)))
                        p2.name = pdat[1].get("name", p2.name)
                        p2.is_ai = pdat[1].get("is_ai", False)
                        p2.cities = set(tuple(t) for t in pdat[1].get("cities", list(p2.cities)))
                    turn_number = data.get("turn_number", turn_number)
                    current_player = data.get("current_player", current_player)
                    # Recompute visibility
                    world.init_fow([p1.name, p2.name])
                    if isinstance(world.explored, dict):
                        # Restore explored; visible will be recomputed
                        world.visible = {p1.name: [[False for _ in range(world.width)] for _ in range(world.height)], p2.name: [[False for _ in range(world.width)] for _ in range(world.height)]}
                    recompute_visibility(world, current_player, units)
                    # Center camera
                    sel = select_next_unit(units, current_player, None)
                    if sel is not None:
                        vx, vy = center_view_on(world, vw, vh, sel.x, sel.y)
                # Set production at city under selected unit, if owned
                if selected is not None and selected.owner == current_player:
                    c = city_at(world, selected.x, selected.y)
                    if c is not None and c.owner == current_player:
                        c.production_type = 'Army'
                        c.production_cost = 8
                        # keep progress
            elif key in (ord(' '),):
                # End turn: production, switch player, reset moves, check victory
                advance_production_and_spawn(world, units)
                # Fighter basing: current player's fighters must be on friendly city
                enforce_fighter_basing(world, units, current_player)
                # Victory check: opponent has zero cities
                opponent = p2.name if current_player == p1.name else p1.name
                opp_city_count = sum(1 for c in world.cities if c.owner == opponent)
                my_city_count = sum(1 for c in world.cities if c.owner == current_player)
                if opp_city_count == 0 and my_city_count > 0:
                    stdscr.addstr(vh, 0, f"{current_player} wins! Press Q to quit."[:vw])
                    stdscr.refresh()
                    # wait for Q
                    while True:
                        k2 = stdscr.getch()
                        if k2 in (ord('q'), ord('Q')):
                            return
                # Handoff screen: hide map and wait for SPACE to start next turn
                stdscr.erase()
                handoff_msg1 = f"Turn over for {current_player}. Hand off to {opponent}."
                handoff_msg2 = "Press SPACE to start your turn."
                # Center messages
                msg_y = vh // 2
                msg_x1 = max(0, (vw - len(handoff_msg1)) // 2)
                msg_x2 = max(0, (vw - len(handoff_msg2)) // 2)
                stdscr.addstr(msg_y, msg_x1, handoff_msg1[:vw])
                stdscr.addstr(msg_y + 1, msg_x2, handoff_msg2[:vw])
                stdscr.refresh()
                while True:
                    k2 = stdscr.getch()
                    if k2 == ord(' '):
                        break
                current_player = opponent
                turn_number += 1
                reset_moves_for_owner(units, current_player)
                selected = select_next_unit(units, current_player, None)
                if selected is not None:
                    vx, vy = center_view_on(world, vw, vh, selected.x, selected.y)
                recompute_visibility(world, current_player, units)

    curses.wrapper(_main)


def run_fallback(world: GameMap, p1: Player, p2: Player, units: List[Unit]) -> None:
    # Text UI with typed commands and a sidebar printed to the right
    sidebar_lines = build_sidebar_lines('fallback')
    sidebar_w = max(len(line) for line in sidebar_lines) + 1
    vw, vh = min(world.width, 60), min(world.height, 20)
    # reduce vw to give space to sidebar in console output
    vw = max(20, vw - sidebar_w)
    vx, vy = 0, 0
    current_player = p1.name
    turn_number = 1
    selected: Optional[Unit] = None
    focused_city_index: Optional[int] = None
    reset_moves_for_owner(units, current_player)
    # Init per-player FoW
    world.init_fow([p1.name, p2.name])
    for c in world.cities:
        if c.owner in (p1.name, p2.name):
            world.mark_visible_circle(c.owner, c.x, c.y, radius=CITY_SIGHT)
    for u in units:
        world.mark_visible_circle(u.owner, u.x, u.y, radius=UNIT_SIGHT)
    recompute_visibility(world, current_player, units)
    # Center on first ready unit on start
    selected = select_next_unit(units, current_player, None)
    if selected is not None:
        vx, vy = center_view_on(world, vw, vh, selected.x, selected.y)
    print("Text UI. Commands: n=next, wasd=move, b=build, e=end, save <name>, load <name>, q=quit; arrows: pan")
    while True:
        view: Viewport = (vx, vy, vw, vh)
        board_lines = render_view(world, view, units, active_player=current_player)
        for row_idx in range(vh):
            left = board_lines[row_idx] if row_idx < len(board_lines) else ""
            right = sidebar_lines[row_idx] if row_idx < len(sidebar_lines) else ""
            print(f"{left:<{vw}} {right}")
        sel_txt = "none" if selected is None else f"{selected.owner} A @({selected.x},{selected.y}) hp:{selected.hp}/{selected.max_hp} mp:{selected.moves_left}"
        # Show city info / ETA if under selected unit
        city_info = ""
        if selected is not None:
            c = city_at(world, selected.x, selected.y)
            if c is not None and c.owner == current_player and c.production_type and c.production_cost > 0:
                eta = max(0, c.production_cost - c.production_progress)
                city_info = f" | City: {c.production_type} ETA {eta}"
        # Focused city info
        focus_info = ""
        if focused_city_index is not None:
            own_cities = [c for c in world.cities if c.owner == current_player]
            if own_cities:
                fc = own_cities[focused_city_index % len(own_cities)]
                eta2 = max(0, (fc.production_cost or 0) - (fc.production_progress or 0)) if fc.production_cost else 0
                ptxt = fc.production_type or "(none)"
                focus_info = f" | Focus: ({fc.x},{fc.y}) {ptxt} ETA {eta2}"
        print(f"P:{current_player} T:{turn_number} | Sel:{sel_txt}{city_info}{focus_info} | n/wasd/b/r/p/c/e/q | arrows pan")
        cmd = input("> ").strip()
        low = cmd.lower()
        if cmd == 'q':
            ans = input("Quit? (y/N) ").strip().lower()
            if ans == 'y':
                break
            else:
                continue
        elif cmd == 'a':
            vx = clamp(vx - 1, 0, max(0, world.width - vw))
        elif cmd == 'd':
            vx = clamp(vx + 1, 0, max(0, world.width - vw))
        elif cmd == 'w':
            vy = clamp(vy - 1, 0, max(0, world.height - vh))
        elif cmd == 's':
            vy = clamp(vy + 1, 0, max(0, world.height - vh))
        elif cmd == 'n':
            selected = select_next_unit_any(units, current_player, selected)
        elif cmd in ('i', 'k', 'j', 'l', 'up', 'down', 'left', 'right', 'move', 'm', 'wasd'):
            # support 'i/j/k/l' or 'w/a/s/d' style by checking exact strings next
            pass
        elif cmd == 'b':
            target_city = None
            if focused_city_index is not None:
                own_cities = [c for c in world.cities if c.owner == current_player]
                if own_cities:
                    target_city = own_cities[focused_city_index % len(own_cities)]
            if target_city is None and selected is not None and selected.owner == current_player:
                target_city = city_at(world, selected.x, selected.y)
            if target_city is not None and target_city.owner == current_player:
                set_city_production(target_city, 'Army')
        elif cmd == 'r':
            target_city = None
            if focused_city_index is not None:
                own_cities = [c for c in world.cities if c.owner == current_player]
                if own_cities:
                    target_city = own_cities[focused_city_index % len(own_cities)]
            if target_city is None and selected is not None and selected.owner == current_player:
                target_city = city_at(world, selected.x, selected.y)
            if target_city is not None and target_city.owner == current_player:
                set_city_production(target_city, 'Fighter')
        elif cmd == 'p':
            target_city = None
            if focused_city_index is not None:
                own_cities = [c for c in world.cities if c.owner == current_player]
                if own_cities:
                    target_city = own_cities[focused_city_index % len(own_cities)]
            if target_city is None and selected is not None and selected.owner == current_player:
                target_city = city_at(world, selected.x, selected.y)
            if target_city is not None and target_city.owner == current_player:
                cycle_city_production(target_city)
        elif cmd == 'c':
            own_cities = [c for c in world.cities if c.owner == current_player]
            if own_cities:
                focused_city_index = 0 if focused_city_index is None else (focused_city_index + 1) % len(own_cities)
                fc = own_cities[focused_city_index]
                vx, vy = center_view_on(world, vw, vh, fc.x, fc.y)
        elif low == 'e':
            advance_production_and_spawn(world, units)
            # Auto-detonate any missiles that have exceeded range or have moves <= 0 (safety)
            for u in list(units):
                if isinstance(u, NuclearMissile) and u.is_alive():
                    # End-turn rule: must detonate regardless of remaining moves
                    det_u, det_c = detonate_missile(world, units, u.owner, u.x, u.y, radius=10)
                    u.hp = 0
            enforce_fighter_basing(world, units, current_player)
            opponent = p2.name if current_player == p1.name else p1.name
            opp_city_count = sum(1 for c in world.cities if c.owner == opponent)
            my_city_count = sum(1 for c in world.cities if c.owner == current_player)
            if opp_city_count == 0 and my_city_count > 0:
                print(f"{current_player} wins!")
                break
            input(f"Turn over for {current_player}. Hand off to {opponent}. Press Enter...")
            current_player = opponent
            turn_number += 1
            reset_moves_for_owner(units, current_player)
            selected = select_next_unit(units, current_player, None)
            if selected is not None:
                vx, vy = center_view_on(world, vw, vh, selected.x, selected.y)
            recompute_visibility(world, current_player, units)
        # Movement commands and skip
        if low in ('i',):
            if selected is None:
                selected = select_next_unit(units, current_player, selected)
            if selected is not None and selected.owner == current_player:
                moved, captured, victory, _ = try_move_unit(world, units, selected, 0, -1)
                if victory:
                    print(f"{current_player} wins!")
                    break
                if moved:
                    recompute_visibility(world, current_player, units)
                    if not (selected is not None and selected.is_alive() and selected.can_move()):
                        selected = select_next_unit(units, current_player, selected)
        elif low in ('k',):
            if selected is None:
                selected = select_next_unit(units, current_player, selected)
            if selected is not None and selected.owner == current_player:
                moved, captured, victory, _ = try_move_unit(world, units, selected, 0, 1)
                if victory:
                    print(f"{current_player} wins!")
                    break
                if moved:
                    recompute_visibility(world, current_player, units)
                    if not (selected is not None and selected.is_alive() and selected.can_move()):
                        selected = select_next_unit(units, current_player, selected)
        elif low in ('j',):
            if selected is None:
                selected = select_next_unit(units, current_player, selected)
            if selected is not None and selected.owner == current_player:
                moved, captured, victory, _ = try_move_unit(world, units, selected, -1, 0)
                if victory:
                    print(f"{current_player} wins!")
                    break
                if moved:
                    recompute_visibility(world, current_player, units)
                    if not (selected is not None and selected.is_alive() and selected.can_move()):
                        selected = select_next_unit(units, current_player, selected)
        elif low in ('l',):
            if selected is None:
                selected = select_next_unit(units, current_player, selected)
            if selected is not None and selected.owner == current_player:
                moved, captured, victory, _ = try_move_unit(world, units, selected, 1, 0)
                if victory:
                    print(f"{current_player} wins!")
                    break
                if moved:
                    recompute_visibility(world, current_player, units)
                    if not (selected is not None and selected.is_alive() and selected.can_move()):
                        selected = select_next_unit(units, current_player, selected)
        elif low in (' ', 'skip', 'wait'):
            selected = select_next_unit(units, current_player, selected)
        elif low in ('d',):
            if selected is not None and isinstance(selected, NuclearMissile) and selected.owner == current_player:
                det_u, det_c = detonate_missile(world, units, current_player, selected.x, selected.y, radius=10)
                selected.hp = 0
                recompute_visibility(world, current_player, units)
                print(f"Nuke: destroyed {det_u} units, neutralized {det_c} cities")
                selected = select_next_unit(units, current_player, None)
        elif low.startswith('save'):
            parts = cmd.split()
            if len(parts) >= 2:
                name = parts[1].strip()
                if name:
                    save_dir = ensure_save_dir()
                    path = os.path.join(save_dir, f"{name}.json")
                    players_data = [
                        {"name": p1.name, "is_ai": p1.is_ai, "cities": list(p1.cities)},
                        {"name": p2.name, "is_ai": p2.is_ai, "cities": list(p2.cities)},
                    ]
                    save_full_game(path, world, units, players_data, turn_number, current_player)
        elif low.startswith('load'):
            parts = cmd.split()
            if len(parts) >= 2:
                name = parts[1].strip()
                if name:
                    save_dir = ensure_save_dir()
                    path = os.path.join(save_dir, f"{name}.json")
                    data = load_full_game(path)
                    loaded_map = data["map"]
                    world.width = loaded_map["width"]
                    world.height = loaded_map["height"]
                    world.tiles = loaded_map["tiles"]
                    world.fog = loaded_map["fog"]
                    world.cities = [City(**c) for c in loaded_map["cities"]]
                    world.explored = loaded_map.get("explored", {})
                    units.clear()
                    for ud in data["units"]:
                        utype = ud.get("unit_type", "Army")
                        if utype == "Fighter":
                            u = Fighter(x=ud["x"], y=ud["y"], owner=ud["owner"]) 
                        elif utype == "Carrier":
                            u = Carrier(x=ud["x"], y=ud["y"], owner=ud["owner"]) 
                        elif utype == "NuclearMissile":
                            u = NuclearMissile(x=ud["x"], y=ud["y"], owner=ud["owner"]) 
                        else:
                            u = Army(x=ud["x"], y=ud["y"], owner=ud["owner"]) 
                        u.symbol = ud.get("symbol", u.symbol)
                        u.max_hp = ud.get("max_hp", u.max_hp)
                        u.hp = ud.get("hp", u.hp)
                        u.movement_points = ud.get("movement_points", u.movement_points)
                        u.fuel = ud.get("fuel", None)
                        u.moves_left = ud.get("moves_left", u.movement_points)
                        u.home_city = tuple(ud["home_city"]) if ud.get("home_city") else None
                        units.append(u)
                    pdat = data.get("players", [])
                    if len(pdat) >= 2:
                        p1.name = pdat[0].get("name", p1.name)
                        p1.is_ai = pdat[0].get("is_ai", False)
                        p1.cities = set(tuple(t) for t in pdat[0].get("cities", list(p1.cities)))
                        p2.name = pdat[1].get("name", p2.name)
                        p2.is_ai = pdat[1].get("is_ai", False)
                        p2.cities = set(tuple(t) for t in pdat[1].get("cities", list(p2.cities)))
                    turn_number = data.get("turn_number", turn_number)
                    current_player = data.get("current_player", current_player)
                    world.init_fow([p1.name, p2.name])
                    if isinstance(world.explored, dict):
                        world.visible = {p1.name: [[False for _ in range(world.width)] for _ in range(world.height)], p2.name: [[False for _ in range(world.width)] for _ in range(world.height)]}
                    recompute_visibility(world, current_player, units)
                    sel = select_next_unit(units, current_player, None)
                    if sel is not None:
                        vx, vy = center_view_on(world, vw, vh, sel.x, sel.y)
            else:
                # List available saves
                save_dir = ensure_save_dir()
                saves = [fn[:-5] for fn in os.listdir(save_dir) if fn.lower().endswith('.json')]
                print("Available saves:", ", ".join(sorted(saves)) if saves else "(none)")
        print("\n" * 1)


def main() -> int:
    # Headless smoke mode for CI / quick verification
    if "--smoke" in sys.argv:
        world, human, ai, units = build_initial_game(width=120, height=48)
        view: Viewport = (0, 0, min(60, world.width), min(15, world.height))
        lines = render_view(world, view, units)
        print("SMOKE: start")
        for line in lines:
            print(line)
        print("SMOKE: end")
        return 0

    world, p1, p2, units = build_initial_game(width=120, height=48)
    if HAS_CURSES:
        try:
            run_curses(world, p1, p2, units)
        except Exception as exc:
            # Fallback if curses fails at runtime
            print(f"Curses error: {exc}. Falling back to text mode.")
            run_fallback(world, p1, p2, units)
    else:
        run_fallback(world, p1, p2, units)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


