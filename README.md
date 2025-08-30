# Game 233 Empire

A faithful, modern Python reproduction of the classic 1977 turn-based strategy game "Empire" (also known as Classic Empire and later variants like Wolfpack Empire). Inspired directly by the detailed gameplay description and walkthrough by Data Driven Gamer: [Game 233: Empire](https://datadrivengamer.blogspot.com/2021/01/game-233-empire.html).

## Vision
- Text-based ASCII wargame where players conquer a randomly generated world.
- Entire map rendered with characters (e.g., `~` for ocean, `.` for land, `O`/`o`/`X` for cities, unit glyphs like `A`, `D`, `F`).
- Single-player vs. AI first, with simulated asynchronous multiplayer (turns saved/loaded like BBS door games) later.
- Objective: capture all cities.

## Features (Roadmap)
- Random map generation with islands and continents, ~60 cities on land.
- Fog of war; unexplored is blank, revealed around units and cities.
- Unit progression: Armies (A), Destroyers (D), Fighters (F, with fuel), Submarines (S), Transports (TT), Carriers (C), Battleships (BB), Nuclear weapons.
- City management: set production, per-turn build times, capture mechanics.
- Turn-based gameplay modes: Orders, Move, Edit; key-driven interaction.
- Probabilistic combat resolution, matchup-based advantages, city conquest odds.
- Interface with `curses` for a retro terminal UI; later optional Pygame.
- Save/load for asynchronous play and replayability.

## Current State (MVP)
- Generates a random ASCII world map.
- Places a handful of cities on land and one player Army unit.
- Renders a scrollable viewport using `curses` when available; otherwise falls back to a simple text UI.
- Fog of war reveals around the player and cities.

## Hot-seat MVP (2 players)
- Two players (P1 and P2) take turns on the same keyboard.
- No fog-of-war (full map visible); cities show ownership (`O` for P1, `X` for P2, `o` for neutral).
- Each player starts with one city and one Army.
- Armies move 1 tile per turn on land only. Entering an enemy/neutral city captures it. Entering an enemy unit’s tile triggers combat.
- Cities can produce Armies (default cost 6 turns). Finished units spawn at the city or adjacent land tile.
- Victory: opponent has no cities.

## Getting Started
### Quick Start (Windows)
- Double-click `Run_Game_233_Empire.bat` in the project folder.
- If the window flashes and closes, open `last_run.log` in the folder for details, or run from PowerShell (see below).

### Run from PowerShell
```powershell
cd "C:\Users\sugar\Desktop\ALL AI GAMES\Projects in prgress\Game 233 Empire"
python main.py
```

### Prerequisites
- Python 3.9+
- On Windows, `curses` is not built-in. The game will automatically fall back to a simple text mode if `curses` is unavailable. You can optionally install `windows-curses` for better terminal support:

```bash
pip install windows-curses
```

### Run (manual)
```bash
python main.py
```
- Curses UI keys:
  - Arrow keys or `h/j/k/l` to pan
  - `N` select next unit, `W/A/S/D` move selected unit
  - `B` set production to Army at owned city under selected unit
  - `E` end turn (hands off to the other player)
  - `Q` quit
- Fallback text UI commands:
  - Pan with `w/a/s/d`
  - `n` select next unit, `i/j/k/l` move selected unit
  - `b` set production to Army at owned city under selected unit
  - `e` end turn (hands off to the other player)
  - `q` quit

### One-Click Launcher (Windows)
- `Run_Game_233_Empire.bat` will:
  - Detect Python (`py` or `python`).
  - Auto-install `windows-curses` on first run if missing.
  - Launch the game and write output to `last_run.log`.
- If something goes wrong, open `last_run.log` to see what happened.

### Command-line Options
- `--smoke` — headless smoke test; prints a one-screen snapshot and exits.

Examples:
```powershell
python main.py --smoke
```

## Project Structure
```
main.py       # Entry point and MVP game loop (map render + panning)
map.py        # World generation, fog of war, city placement/rendering
units.py      # Unit classes (Army implemented; others stubbed)
combat.py     # Combat resolution (stubbed placeholder)
player.py     # Player model and AI placeholder
savegame.py   # JSON serialization helpers (stubbed for future)
README.md     # This file
Run_Game_233_Empire.bat  # Windows launcher (double-click to start)
```

## Development Plan (Sprints)
- Sprint 1: Map
  - World generation (continents/islands), ~60 city placement, fog-of-war, viewport rendering.
  - Performance and map tuning; color ANSI polish.
- Sprint 2: Units & Production
  - Full unit hierarchy with stats (HP, movement, fuel).
  - City production menus and per-turn progress.
- Sprint 3: Turns & Movement
  - Turn-based modes (Orders/Move/Edit), waypointing and sentry/wake systems.
  - Movement rules (land/sea/air), terrain constraints, supply/fuel.
- Sprint 4: Combat
  - Probabilistic combat with matchup tables and city conquest loop feel (~50% odds, repeated attempts).
  - Naval/air dominance rules, submarines vs destroyers tuning, nuclear fallout effects on map.
- Sprint 5: AI & Polish
  - Simple AI for expansion and city targeting; async save/load like BBS door games.
  - Balancing, UX refinement, colors, optional Pygame frontend.

## Notes on Authenticity
- The design references the Data Driven Gamer blog post to capture original mechanics and feel while applying modern code structure and testability.
- Exact numeric balance will evolve during development but aims to preserve the strategic character described.

## Testing
- Modules are designed to be importable and testable (e.g., `GameMap.generate`, `resolve_attack`).
- Future: add unit tests for generation, fog-of-war, and combat odds.

## Controls
- Curses UI: Pan with arrows or `h/j/k/l`; `N` next unit; `W/A/S/D` move; `B` build Army; `E` end turn; `Q` quit.
- Fallback text UI: Pan with `w/a/s/d`; `n` next unit; `i/j/k/l` move; `b` build Army; `e` end turn; `q` quit.

## Troubleshooting (Windows)
- Window opens and closes instantly when double-clicking:
  - Run from PowerShell to see messages:
    ```powershell
    cd "C:\Users\sugar\Desktop\ALL AI GAMES\Projects in prgress\Game 233 Empire"
    .\Run_Game_233_Empire.bat
    ```
  - Check `last_run.log` for errors.
- Python not found:
  - Install Python from `https://www.python.org/` and ensure it’s on PATH, or use the `py` launcher.
- Arrow keys don’t work / no colors:
  - Install `windows-curses`:
    ```powershell
    py -m pip install --user windows-curses
    ```
  - Then run `python main.py` again.

## Future Extensions
- Multiplayer via sockets or hotseat with turn files.
- Richer UI with Pygame; zoomed maps; mouse support.
- Scenario generation and map seeds.

## License
- TBD (choose MIT or similar for permissive use).
