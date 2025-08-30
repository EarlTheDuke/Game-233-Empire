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


Viewport = Tuple[int, int, int, int]  # x, y, width, height


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

    # Hot-seat: reveal full map
    world.reveal_all()

    # Initialize default production values for owned cities (optional)
    for c in world.cities:
        if c.owner in (p1.name, p2.name):
            c.production_type = "Army"
            c.production_cost = 6
            c.production_progress = 0

    return world, p1, p2, units


def render_view(world: GameMap, view: Viewport, units: List[Unit]) -> List[str]:
    base = world.render(view)
    return overlay_units_on_buffer(base, view, units)


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


def try_capture_city(world: GameMap, unit: Unit) -> None:
    c = city_at(world, unit.x, unit.y)
    if c is None:
        return
    if c.owner != unit.owner:
        c.owner = unit.owner


def try_move_unit(world: GameMap, units: List[Unit], u: Unit, dx: int, dy: int) -> None:
    if not u.can_move():
        return
    nx, ny = u.x + dx, u.y + dy
    if not is_land(world, nx, ny):
        return
    blocking = unit_at(units, nx, ny)
    if blocking is not None:
        if blocking.owner == u.owner:
            return  # cannot stack
        # combat
        attacker_alive, defender_alive = resolve_attack(u, blocking)
        if not defender_alive:
            # remove defender; move in if attacker alive
            blocking.hp = 0
            if attacker_alive:
                u.x, u.y = nx, ny
                u.moves_left -= 1
                try_capture_city(world, u)
        else:
            # defender survived; attacker may have died
            if not attacker_alive:
                u.hp = 0
        return
    # Move into empty land
    u.x, u.y = nx, ny
    u.moves_left -= 1
    try_capture_city(world, u)


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
                ]
                placed = False
                for (sx, sy) in spawn_positions:
                    if 0 <= sx < world.width and 0 <= sy < world.height:
                        if is_land(world, sx, sy) and unit_at(units, sx, sy) is None:
                            nu = Army(x=sx, y=sy, owner=c.owner)
                            nu.reset_moves()
                            units.append(nu)
                            placed = True
                            break
                if placed:
                    c.production_progress = 0
                else:
                    # try again next turn
                    c.production_progress = c.production_cost  # stay ready


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
        vw = max(20, min(world.width, max_x))
        vh = max(10, min(world.height, max_y - 1))
        vx, vy = 0, 0
        reset_moves_for_owner(units, current_player)

        while True:
            view: Viewport = (vx, vy, vw, vh)
            lines = render_view(world, view, units)
            stdscr.erase()
            for row_idx, line in enumerate(lines[:vh]):
                stdscr.addstr(row_idx, 0, line[:vw])

            city_under_view: Optional[City] = None
            # Status: player, turn, selected unit, hint keys
            sel_txt = "none"
            if selected is not None and selected.is_alive():
                sel_txt = f"{selected.owner} A @({selected.x},{selected.y}) mp:{selected.moves_left}"
            status = (
                f"P:{current_player} T:{turn_number} | Units:{len([u for u in units if u.is_alive()])} "
                f"Sel:{sel_txt} | Keys: N-next WASD-move B-build E-end Q-quit | Arrows pan"
            )
            stdscr.addstr(vh, 0, status[:vw])
            stdscr.refresh()

            key = stdscr.getch()
            if key in (ord('q'), ord('Q')):
                break
            elif key in (curses.KEY_LEFT, ord('h')):
                vx = clamp(vx - 1, 0, max(0, world.width - vw))
            elif key in (curses.KEY_RIGHT, ord('l')):
                vx = clamp(vx + 1, 0, max(0, world.width - vw))
            elif key in (curses.KEY_UP, ord('k')):
                vy = clamp(vy - 1, 0, max(0, world.height - vh))
            elif key in (curses.KEY_DOWN, ord('j')):
                vy = clamp(vy + 1, 0, max(0, world.height - vh))
            elif key in (ord('n'), ord('N')):
                selected = select_next_unit(units, current_player, selected)
            elif key in (ord('w'), ord('W')):
                if selected is None:
                    selected = select_next_unit(units, current_player, selected)
                if selected is not None and selected.owner == current_player:
                    try_move_unit(world, units, selected, 0, -1)
            elif key in (ord('s'), ord('S')):
                if selected is None:
                    selected = select_next_unit(units, current_player, selected)
                if selected is not None and selected.owner == current_player:
                    try_move_unit(world, units, selected, 0, 1)
            elif key in (ord('a'), ord('A')):
                if selected is None:
                    selected = select_next_unit(units, current_player, selected)
                if selected is not None and selected.owner == current_player:
                    try_move_unit(world, units, selected, -1, 0)
            elif key in (ord('d'), ord('D')):
                if selected is None:
                    selected = select_next_unit(units, current_player, selected)
                if selected is not None and selected.owner == current_player:
                    try_move_unit(world, units, selected, 1, 0)
            elif key in (ord('b'), ord('B')):
                # Set production at city under selected unit, if owned
                if selected is not None and selected.owner == current_player:
                    c = city_at(world, selected.x, selected.y)
                    if c is not None and c.owner == current_player:
                        c.production_type = 'Army'
                        c.production_cost = 6
                        # keep progress
            elif key in (ord('e'), ord('E')):
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
                # Switch player
                # Handoff prompt
                stdscr.addstr(vh, 0, f"Turn over for {current_player}. Hand off to {opponent}. Press any key..."[:vw])
                stdscr.refresh()
                stdscr.getch()
                current_player = opponent
                turn_number += 1
                reset_moves_for_owner(units, current_player)
                selected = None

    curses.wrapper(_main)


def run_fallback(world: GameMap, p1: Player, p2: Player, units: List[Unit]) -> None:
    # Text UI with typed commands
    vw, vh = min(world.width, 60), min(world.height, 20)
    vx, vy = 0, 0
    current_player = p1.name
    turn_number = 1
    selected: Optional[Unit] = None
    reset_moves_for_owner(units, current_player)
    print("Text UI. Commands: n=next, wasd=move, b=build, e=end, q=quit; arrows: pan")
    while True:
        view: Viewport = (vx, vy, vw, vh)
        for line in render_view(world, view, units):
            print(line)
        sel_txt = "none" if selected is None else f"{selected.owner} A @({selected.x},{selected.y}) mp:{selected.moves_left}"
        print(f"P:{current_player} T:{turn_number} | Sel:{sel_txt} | Cities:{len(world.cities)} | n/wasd/b/e/q | arrows pan")
        cmd = input("> ").strip().lower()
        if cmd == 'q':
            break
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
                    c.production_cost = 6
        elif cmd == 'e':
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
            selected = None
        # Movement commands
        if cmd in ('i',):
            if selected is None:
                selected = select_next_unit(units, current_player, selected)
            if selected is not None and selected.owner == current_player:
                try_move_unit(world, units, selected, 0, -1)
        elif cmd in ('k',):
            if selected is None:
                selected = select_next_unit(units, current_player, selected)
            if selected is not None and selected.owner == current_player:
                try_move_unit(world, units, selected, 0, 1)
        elif cmd in ('j',):
            if selected is None:
                selected = select_next_unit(units, current_player, selected)
            if selected is not None and selected.owner == current_player:
                try_move_unit(world, units, selected, -1, 0)
        elif cmd in ('l',):
            if selected is None:
                selected = select_next_unit(units, current_player, selected)
            if selected is not None and selected.owner == current_player:
                try_move_unit(world, units, selected, 1, 0)
        print("\n" * 1)


def main() -> int:
    # Headless smoke mode for CI / quick verification
    if "--smoke" in sys.argv:
        world, human, ai, units = build_initial_game(width=60, height=24)
        view: Viewport = (0, 0, min(60, world.width), min(15, world.height))
        lines = render_view(world, view, units)
        print("SMOKE: start")
        for line in lines:
            print(line)
        print("SMOKE: end")
        return 0

    world, p1, p2, units = build_initial_game(width=60, height=24)
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


