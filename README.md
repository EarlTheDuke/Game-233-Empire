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

## Getting Started
### Prerequisites
- Python 3.9+
- On Windows, `curses` is not built-in. The game will automatically fall back to a simple text mode if `curses` is unavailable. You can optionally install `windows-curses` for better terminal support:

```bash
pip install windows-curses
```

### Run
```bash
python main.py
```
- Use arrow keys or `h/j/k/l` to pan (in curses mode), or `w/a/s/d` in fallback mode.
- Press `Q` to quit.

## Project Structure
```
main.py       # Entry point and MVP game loop (map render + panning)
map.py        # World generation, fog of war, city placement/rendering
units.py      # Unit classes (Army implemented; others stubbed)
combat.py     # Combat resolution (stubbed placeholder)
player.py     # Player model and AI placeholder
savegame.py   # JSON serialization helpers (stubbed for future)
README.md     # This file
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

## Future Extensions
- Multiplayer via sockets or hotseat with turn files.
- Richer UI with Pygame; zoomed maps; mouse support.
- Scenario generation and map seeds.

## License
- TBD (choose MIT or similar for permissive use).
