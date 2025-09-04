from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


class Terrain:
    OCEAN = '.'
    LAND = '+'  # Land now shown as '+'; water as '.'


@dataclass
class City:
    x: int
    y: int
    owner: Optional[str] = None  # None or 'neutral' indicates neutral
    # Hot-seat MVP production fields
    production_type: Optional[str] = None  # e.g., 'Army'
    production_progress: int = 0
    production_cost: int = 0
    # Support cap
    support_cap: int = 2

    def symbol(self) -> str:
        if self.owner is None or self.owner == 'neutral':
            return 'o'
        # Two-player hot-seat glyphs
        if self.owner in ("P2", "Player 2"):
            return 'X'
        # Player 1 or any other named owner
        return 'O'


class GameMap:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.tiles: List[List[str]] = [[Terrain.OCEAN for _ in range(width)] for _ in range(height)]
        self.cities: List[City] = []
        # Legacy fog retained for reference; per-player FoW below
        self.fog: List[List[bool]] = [[True for _ in range(width)] for _ in range(height)]
        # Per-player fog of war
        self.explored: Dict[str, List[List[bool]]] = {}
        self.visible: Dict[str, List[List[bool]]] = {}

    # --- Generation ---
    def generate(self, seed: Optional[int] = None, land_target: float = 0.55) -> None:
        rng = random.Random(seed)
        # Start with random noise
        noise: List[List[float]] = [[rng.random() for _ in range(self.width)] for _ in range(self.height)]

        # Smooth noise with a few cellular automata passes to form blobs
        def count_landish(y: int, x: int) -> int:
            total = 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dy == 0 and dx == 0:
                        continue
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < self.height and 0 <= nx < self.width:
                        total += 1 if noise[ny][nx] > 0.5 else 0
            return total

        for _ in range(4):
            new_noise = [[v for v in row] for row in noise]
            for y in range(self.height):
                for x in range(self.width):
                    n = count_landish(y, x)
                    if n >= 5:
                        new_noise[y][x] = min(1.0, noise[y][x] + 0.2)
                    elif n <= 3:
                        new_noise[y][x] = max(0.0, noise[y][x] - 0.2)
            noise = new_noise

        # Threshold to achieve approximate land ratio (bias toward more land for easier contact)
        flat = [v for row in noise for v in row]
        flat_sorted = sorted(flat)
        idx = int((1.0 - land_target) * len(flat_sorted))
        threshold = flat_sorted[idx]

        for y in range(self.height):
            for x in range(self.width):
                self.tiles[y][x] = Terrain.LAND if noise[y][x] >= threshold else Terrain.OCEAN

        # Clean tiny lakes/peninsulas with a final pass
        for _ in range(2):
            self._smooth_terrain()
        # Ensure a single connected landmass (carve corridors between components)
        self._ensure_connected_land()

    def _smooth_terrain(self) -> None:
        def neighbors(y: int, x: int) -> Tuple[int, int]:
            land = 0
            water = 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dy == 0 and dx == 0:
                        continue
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < self.height and 0 <= nx < self.width:
                        if self.tiles[ny][nx] == Terrain.LAND:
                            land += 1
                        else:
                            water += 1
            return land, water

        for y in range(self.height):
            for x in range(self.width):
                land, water = neighbors(y, x)
                if land >= 5:
                    self.tiles[y][x] = Terrain.LAND
                elif water >= 5:
                    self.tiles[y][x] = Terrain.OCEAN

    def _ensure_connected_land(self) -> None:
        # Identify connected components of land tiles and connect them to the largest
        visited = [[False for _ in range(self.width)] for _ in range(self.height)]
        components: List[List[Tuple[int, int]]] = []

        def bfs(start_y: int, start_x: int) -> List[Tuple[int, int]]:
            q: List[Tuple[int, int]] = [(start_y, start_x)]
            visited[start_y][start_x] = True
            comp: List[Tuple[int, int]] = [(start_x, start_y)]
            while q:
                cy, cx = q.pop(0)
                for dy, dx in ((1,0),(-1,0),(0,1),(0,-1)):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < self.height and 0 <= nx < self.width and not visited[ny][nx]:
                        if self.tiles[ny][nx] == Terrain.LAND:
                            visited[ny][nx] = True
                            q.append((ny, nx))
                            comp.append((nx, ny))
            return comp

        for y in range(self.height):
            for x in range(self.width):
                if not visited[y][x] and self.tiles[y][x] == Terrain.LAND:
                    components.append(bfs(y, x))

        if len(components) <= 1:
            return

        # Choose largest component as the main landmass
        components.sort(key=lambda c: len(c), reverse=True)
        main_comp = components[0]
        main_rep_x, main_rep_y = main_comp[0]

        def carve_path(x0: int, y0: int, x1: int, y1: int) -> None:
            x, y = x0, y0
            # Manhattan carve from (x0,y0) to (x1,y1)
            while x != x1:
                self.tiles[y][x] = Terrain.LAND
                x += 1 if x1 > x else -1
            while y != y1:
                self.tiles[y][x] = Terrain.LAND
                y += 1 if y1 > y else -1
            self.tiles[y][x] = Terrain.LAND

        # Connect each smaller component to the main landmass via simple corridor
        for comp in components[1:]:
            cx, cy = comp[0]
            carve_path(cx, cy, main_rep_x, main_rep_y)

    def place_cities(self, count: int = 20, min_separation: int = 3) -> None:
        land_positions = [(x, y) for y in range(self.height) for x in range(self.width) if self.tiles[y][x] == Terrain.LAND]
        rng = random.Random()
        rng.shuffle(land_positions)
        placed: List[Tuple[int, int]] = []

        def far_enough(x: int, y: int) -> bool:
            for px, py in placed:
                if abs(px - x) + abs(py - y) < min_separation:
                    return False
            return True

        for (x, y) in land_positions:
            if len(placed) >= count:
                break
            if far_enough(x, y):
                self.cities.append(City(x=x, y=y, owner=None))
                placed.append((x, y))

    # --- Fog of War ---
    def reveal(self, x: int, y: int, radius: int = 3) -> None:
        r2 = radius * radius
        for yy in range(max(0, y - radius), min(self.height, y + radius + 1)):
            for xx in range(max(0, x - radius), min(self.width, x + radius + 1)):
                if (xx - x) * (xx - x) + (yy - y) * (yy - y) <= r2:
                    self.fog[yy][xx] = False

    def reveal_all(self) -> None:
        # Disable fog-of-war for hot-seat MVP
        for y in range(self.height):
            for x in range(self.width):
                self.fog[y][x] = False

    # --- Per-player FoW helpers ---
    def init_fow(self, players: List[str]) -> None:
        self.explored = {
            p: [[False for _ in range(self.width)] for _ in range(self.height)] for p in players
        }
        self.visible = {
            p: [[False for _ in range(self.width)] for _ in range(self.height)] for p in players
        }

    def clear_visible_for(self, player: str) -> None:
        if player not in self.visible:
            return
        v = self.visible[player]
        for y in range(self.height):
            row = v[y]
            for x in range(self.width):
                row[x] = False

    def mark_visible_circle(self, player: str, x: int, y: int, radius: int) -> None:
        if player not in self.visible or player not in self.explored:
            return
        r2 = radius * radius
        for yy in range(max(0, y - radius), min(self.height, y + radius + 1)):
            for xx in range(max(0, x - radius), min(self.width, x + radius + 1)):
                if (xx - x) * (xx - x) + (yy - y) * (yy - y) <= r2:
                    self.visible[player][yy][xx] = True
                    self.explored[player][yy][xx] = True

    # --- Rendering ---
    def render(self, view: Tuple[int, int, int, int], active_player: Optional[str] = None) -> List[str]:
        vx, vy, vw, vh = view
        lines: List[str] = []
        city_map = {(c.x, c.y): c for c in self.cities}
        for y in range(vy, min(self.height, vy + vh)):
            row_chars: List[str] = []
            for x in range(vx, min(self.width, vx + vw)):
                # Per-player FoW if active_player provided
                if active_player is not None and active_player in self.explored and active_player in self.visible:
                    if not self.explored[active_player][y][x]:
                        row_chars.append(' ')
                        continue
                    ch = self.tiles[y][x]
                    if (x, y) in city_map:
                        if self.visible[active_player][y][x]:
                            ch = city_map[(x, y)].symbol()
                        else:
                            ch = 'o'  # unknown ownership city
                else:
                    # Legacy single-fog behavior
                    if self.fog[y][x]:
                        row_chars.append(' ')
                        continue
                    ch = self.tiles[y][x]
                    if (x, y) in city_map:
                        ch = city_map[(x, y)].symbol()
                row_chars.append(ch)
            lines.append("".join(row_chars))
        return lines

    # --- Utility ---
    def find_spawn_for_player(self, player: str) -> Tuple[int, int]:
        # Prefer player's city if any
        for c in self.cities:
            if c.owner == player:
                return c.x, c.y
        # Otherwise find any land tile near center
        cx, cy = self.width // 2, self.height // 2
        best = (cx, cy)
        best_dist = 1e9
        for y in range(self.height):
            for x in range(self.width):
                if self.tiles[y][x] == Terrain.LAND:
                    d = (x - cx) * (x - cx) + (y - cy) * (y - cy)
                    if d < best_dist:
                        best = (x, y)
                        best_dist = d
        return best


