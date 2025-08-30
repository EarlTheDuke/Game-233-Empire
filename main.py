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
from typing import Dict, List, Tuple

try:
    import curses  # type: ignore
    HAS_CURSES = True
except Exception:
    curses = None  # type: ignore
    HAS_CURSES = False

from map import GameMap
from units import Army, Unit
from player import Player


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
                canvas[uy][ux] = u.symbol
    return ["".join(row) for row in canvas]


def build_initial_game(width: int = 60, height: int = 24) -> Tuple[GameMap, Player, Player, List[Unit]]:
    world = GameMap(width, height)
    world.generate(seed=None)
    world.place_cities(count=12, min_separation=3)

    human = Player(name="Human", is_ai=False)
    ai = Player(name="AI", is_ai=True)

    # Give one starter city to each if available
    if world.cities:
        # First city to human, last city to AI for visual separation
        world.cities[0].owner = human.name
        human.cities.add((world.cities[0].x, world.cities[0].y))
        world.cities[-1].owner = ai.name
        ai.cities.add((world.cities[-1].x, world.cities[-1].y))

    # Spawn one army for the human at their city or nearest land tile
    spawn_x, spawn_y = world.find_spawn_for_player(human.name)
    player_units: List[Unit] = [Army(x=spawn_x, y=spawn_y, owner=human.name)]

    # Reveal around starter locations
    world.reveal(spawn_x, spawn_y, radius=5)
    for (cx, cy) in human.cities:
        world.reveal(cx, cy, radius=5)

    return world, human, ai, player_units


def render_view(world: GameMap, view: Viewport, units: List[Unit]) -> List[str]:
    base = world.render(view)
    return overlay_units_on_buffer(base, view, units)


def run_curses(world: GameMap, human: Player, ai: Player, units: List[Unit]) -> None:
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

        while True:
            view: Viewport = (vx, vy, vw, vh)
            lines = render_view(world, view, units)
            stdscr.erase()
            for row_idx, line in enumerate(lines[:vh]):
                stdscr.addstr(row_idx, 0, line[:vw])

            status = (
                f"Game 233 Empire | View ({vx},{vy}) {vw}x{vh} | Cities: {len(world.cities)} | "
                f"Q=Quit"
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

    curses.wrapper(_main)


def run_fallback(world: GameMap, human: Player, ai: Player, units: List[Unit]) -> None:
    # Simple CLI fallback; type commands to pan (w/a/s/d), q to quit
    vw, vh = min(world.width, 60), min(world.height, 20)
    vx, vy = 0, 0
    print("Curses not available. Using fallback text UI. Type w/a/s/d to pan, q to quit.")
    while True:
        view: Viewport = (vx, vy, vw, vh)
        for line in render_view(world, view, units):
            print(line)
        print(f"View({vx},{vy}) {vw}x{vh} | Cities: {len(world.cities)} | q to quit")
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
        print("\n" * 2)


def main() -> int:
    world, human, ai, units = build_initial_game(width=60, height=24)
    if HAS_CURSES:
        try:
            run_curses(world, human, ai, units)
        except Exception as exc:
            # Fallback if curses fails at runtime
            print(f"Curses error: {exc}. Falling back to text mode.")
            run_fallback(world, human, ai, units)
    else:
        run_fallback(world, human, ai, units)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


