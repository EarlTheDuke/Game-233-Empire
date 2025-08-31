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
from units import Army, Unit
from player import Player
from combat import resolve_attack
from savegame import save_full_game, load_full_game


Viewport = Tuple[int, int, int, int]  # x, y, width, height

# Visibility radii (can be tuned)
UNIT_SIGHT = 3
CITY_SIGHT = 5


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
                # Distinguish owners minimally: P1 'A', P2 'a'
                canvas[uy][ux] = 'A' if u.owner == 'P1' else 'a'
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
            c.production_cost = 8
            c.production_progress = 0

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
    return world.tiles[y][x] == '.'


def try_capture_city(world: GameMap, unit: Unit) -> bool:
    c = city_at(world, unit.x, unit.y)
    if c is None:
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
    if not is_land(world, nx, ny):
        return False, False, False, ""
    blocking = unit_at(units, nx, ny)
    if blocking is not None:
        if blocking.owner == u.owner:
            return False, False, False, ""  # cannot stack
        # combat
        # Apply city defense bonus: defender in city gets +0.10, attacker -0.10
        a_hit = 0.55
        d_hit = 0.50
        cdef = city_at(world, nx, ny)
        if cdef is not None and cdef.owner == blocking.owner:
            a_hit -= 0.10
            d_hit += 0.10
        attacker_alive, defender_alive = resolve_attack(u, blocking, attacker_hit=a_hit, defender_hit=d_hit)
        if not defender_alive:
            # remove defender; move in if attacker alive
            blocking.hp = 0
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
        return True, False, False, "Attacker destroyed"
    # Move into empty land
    u.x, u.y = nx, ny
    u.moves_left -= 1
    captured = try_capture_city(world, u)
    if captured:
        opponent = 'P2' if u.owner == 'P1' else 'P1'
        opp_city_count = sum(1 for c in world.cities if c.owner == opponent)
        my_city_count = sum(1 for c in world.cities if c.owner == u.owner)
        return True, True, (opp_city_count == 0 and my_city_count > 0), "City captured"
    return True, False, False, ""


def advance_production_and_spawn(world: GameMap, units: List[Unit]) -> None:
    for c in world.cities:
        if c.owner is None or c.owner == 'neutral':
            continue
        if c.production_type == 'Army' and c.production_cost > 0:
            c.production_progress += 1
            if c.production_progress >= c.production_cost:
                # Try to spawn at city tile or adjacent land tile
                spawn_positions: List[Tuple[int, int]] = [
                    (c.x, c.y),
                    (c.x + 1, c.y), (c.x - 1, c.y), (c.x, c.y + 1), (c.x, c.y - 1),
                    (c.x + 1, c.y + 1), (c.x - 1, c.y - 1), (c.x + 1, c.y - 1), (c.x - 1, c.y + 1),
                ]
                # Enforce support cap: count armies supported by this city
                def is_supported_by_city(u: Unit, city: City) -> bool:
                    return isinstance(u, Army) and u.owner == city.owner and u.home_city == (city.x, city.y)
                supported = sum(1 for u in units if is_supported_by_city(u, c) and u.is_alive())
                if supported >= c.support_cap:
                    # Stay ready; try next turn (if an army dies, production can proceed)
                    c.production_progress = c.production_cost
                    continue
                placed = False
                for (sx, sy) in spawn_positions:
                    if 0 <= sx < world.width and 0 <= sy < world.height:
                        if is_land(world, sx, sy) and unit_at(units, sx, sy) is None:
                            nu = Army(x=sx, y=sy, owner=c.owner)
                            nu.reset_moves()
                            nu.home_city = (c.x, c.y)
                            units.append(nu)
                            placed = True
                            break
                if placed:
                    c.production_progress = 0
                else:
                    # try again next turn
                    c.production_progress = c.production_cost  # stay ready

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


def recompute_visibility(world: GameMap, owner: str, units: List[Unit]) -> None:
    # Clear and mark for the owner based on cities and units
    world.clear_visible_for(owner)
    for c in world.cities:
        if c.owner == owner:
            world.mark_visible_circle(owner, c.x, c.y, radius=CITY_SIGHT)
    for u in units:
        if u.owner == owner and u.is_alive():
            world.mark_visible_circle(owner, u.x, u.y, radius=UNIT_SIGHT)


def build_sidebar_lines(ui: str) -> List[str]:
    # ui: 'curses' or 'fallback' for key differences
    if ui == 'curses':
        commands = [
            "Commands:",
            " N  Next unit",
            " Arrows Move unit",
            " B  Build Army",
            " S  Save, L Load",
            " Space End turn",
            " Q  Quit",
            " Pan: H/J/K/L",
        ]
    else:
        commands = [
            "Commands:",
            " n  Next unit",
            " i/j/k/l Move",
            " b  Build Army",
            " e  End turn",
            " q  Quit",
            " Pan: w/a/s/d",
        ]
    terrain = [
        "",
        "Terrain:",
        " .  Land",
        " ~  Ocean",
        " O  City (P1)",
        " X  City (P2)",
        " o  City (Neutral)",
    ]
    units_help = [
        "",
        "Units:",
        " A  Army (P1)",
        " a  Army (P2)",
    ]
    return commands + terrain + units_help


def center_view_on(world: GameMap, vw: int, vh: int, target_x: int, target_y: int) -> Tuple[int, int]:
    vx = clamp(target_x - vw // 2, 0, max(0, world.width - vw))
    vy = clamp(target_y - vh // 2, 0, max(0, world.height - vh))
    return vx, vy


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
            view: Viewport = (vx, vy, vw, vh)
            lines = render_view(world, view, units, active_player=current_player)
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
                # right-side sidebar content
                if row_idx < len(sidebar_lines):
                    stdscr.addstr(row_idx, vw + 1, sidebar_lines[row_idx][:sidebar_w - 1])

            city_under_view: Optional[City] = None
            # Status: player, turn, selected unit, hint keys
            sel_txt = "none"
            if selected is not None and selected.is_alive():
                sel_txt = f"{selected.owner} A @({selected.x},{selected.y}) hp:{selected.hp}/{selected.max_hp} mp:{selected.moves_left}"
            # City info under selected unit
            city_info = ""
            if selected is not None and selected.is_alive():
                c = city_at(world, selected.x, selected.y)
                if c is not None and c.owner == current_player and c.production_type == 'Army' and c.production_cost > 0:
                    eta = max(0, c.production_cost - c.production_progress)
                    city_info = f" | City: Army ETA {eta}"
            status = (
                f"P:{current_player} T:{turn_number} | Units:{len([u for u in units if u.is_alive()])} "
                f"Sel:{sel_txt}{city_info}"
            )
            stdscr.addstr(vh, 0, status[:vw])
            stdscr.refresh()

            key = stdscr.getch()
            if key in (ord('q'), ord('Q')):
                # confirm quit if game in progress
                stdscr.addstr(vh, 0, "Quit? (y/N)"[:vw])
                stdscr.refresh()
                k2 = stdscr.getch()
                if k2 in (ord('y'), ord('Y')):
                    break
                else:
                    continue
            elif key in (ord('h'),):
                vx = clamp(vx - 1, 0, max(0, world.width - vw))
            elif key in (ord('l'),):
                vx = clamp(vx + 1, 0, max(0, world.width - vw))
            elif key in (ord('k'),):
                vy = clamp(vy - 1, 0, max(0, world.height - vh))
            elif key in (ord('j'),):
                vy = clamp(vy + 1, 0, max(0, world.height - vh))
            elif key in (ord('n'), ord('N')):
                selected = select_next_unit(units, current_player, selected)
            elif key == curses.KEY_UP:
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
                        # auto-select next ready unit
                        selected = select_next_unit(units, current_player, selected)
            elif key == curses.KEY_DOWN:
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
                        selected = select_next_unit(units, current_player, selected)
            elif key == curses.KEY_LEFT:
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
                        selected = select_next_unit(units, current_player, selected)
            elif key == curses.KEY_RIGHT:
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
                        selected = select_next_unit(units, current_player, selected)
            elif key in (ord('s'), ord('S')):
                # Save game prompt (moved after arrow handling)
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
                    path = f"{name}.json"
                    players_data = [
                        {"name": p1.name, "is_ai": p1.is_ai, "cities": list(p1.cities)},
                        {"name": p2.name, "is_ai": p2.is_ai, "cities": list(p2.cities)},
                    ]
                    save_full_game(path, world, units, players_data, turn_number, current_player)
                    stdscr.move(vh, 0)
                    stdscr.clrtoeol()
                    stdscr.addstr(vh, 0, f"Saved to {path}"[:vw])
                    stdscr.refresh()
            # Space now ends turn (see below)
            elif key in (ord('b'), ord('B')):
                # Set production at city under selected unit, if owned
                if selected is not None and selected.owner == current_player:
                    c = city_at(world, selected.x, selected.y)
                    if c is not None and c.owner == current_player:
                        c.production_type = 'Army'
                        c.production_cost = 8
                        # keep progress
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
                    save_dir = os.path.join(os.getcwd(), "saved games")
                    os.makedirs(save_dir, exist_ok=True)
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
            elif key in (ord('l'), ord('L')):
                prompt = "Load name (no extension): "
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
                    save_dir = os.path.join(os.getcwd(), "saved games")
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
                        u = Army(x=ud["x"], y=ud["y"], owner=ud["owner"])  # Only Army for now
                        u.symbol = ud.get("symbol", "A")
                        u.max_hp = ud.get("max_hp", 10)
                        u.hp = ud.get("hp", 10)
                        u.movement_points = ud.get("movement_points", 1)
                        u.fuel = ud.get("fuel")
                        u.moves_left = ud.get("moves_left", 1)
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
            if c is not None and c.owner == current_player and c.production_type == 'Army' and c.production_cost > 0:
                eta = max(0, c.production_cost - c.production_progress)
                city_info = f" | City: Army ETA {eta}"
        print(f"P:{current_player} T:{turn_number} | Sel:{sel_txt}{city_info} | n/wasd/b/e/q | arrows pan")
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
            selected = select_next_unit(units, current_player, selected)
        elif cmd in ('i', 'k', 'j', 'l', 'up', 'down', 'left', 'right', 'move', 'm', 'wasd'):
            # support 'i/j/k/l' or 'w/a/s/d' style by checking exact strings next
            pass
        elif cmd == 'b':
            if selected is not None and selected.owner == current_player:
                c = city_at(world, selected.x, selected.y)
                if c is not None and c.owner == current_player:
                    c.production_type = 'Army'
                    c.production_cost = 8
        elif low == 'e':
            advance_production_and_spawn(world, units)
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
                    selected = select_next_unit(units, current_player, selected)
        elif low in (' ', 'skip', 'wait'):
            selected = select_next_unit(units, current_player, selected)
        elif low.startswith('save'):
            parts = cmd.split()
            if len(parts) >= 2:
                name = parts[1].strip()
                if name:
                    save_dir = os.path.join(os.getcwd(), "saved games")
                    os.makedirs(save_dir, exist_ok=True)
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
                    save_dir = os.path.join(os.getcwd(), "saved games")
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
                        u = Army(x=ud["x"], y=ud["y"], owner=ud["owner"])  # Only Army for now
                        u.symbol = ud.get("symbol", "A")
                        u.max_hp = ud.get("max_hp", 10)
                        u.hp = ud.get("hp", 10)
                        u.movement_points = ud.get("movement_points", 1)
                        u.fuel = ud.get("fuel")
                        u.moves_left = ud.get("moves_left", 1)
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


