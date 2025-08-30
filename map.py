from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import List, Optional, Tuple


class Terrain:
    OCEAN = '~'
    LAND = '.'  # Keep simple; can evolve to '+' or '*' variants later


@dataclass
class City:
    x: int
    y: int
    owner: Optional[str] = None  # None or 'neutral' indicates neutral
    # Hot-seat MVP production fields
    production_type: Optional[str] = None  # e.g., 'Army'
    production_progress: int = 0
    production_cost: int = 0

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
        self.fog: List[List[bool]] = [[True for _ in range(width)] for _ in range(height)]

    # --- Generation ---
    def generate(self, seed: Optional[int] = None, land_target: float = 0.45) -> None:
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

        # Threshold to achieve approximate land ratio
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

    # --- Rendering ---
    def render(self, view: Tuple[int, int, int, int]) -> List[str]:
        vx, vy, vw, vh = view
        lines: List[str] = []
        city_map = {(c.x, c.y): c for c in self.cities}
        for y in range(vy, min(self.height, vy + vh)):
            row_chars: List[str] = []
            for x in range(vx, min(self.width, vx + vw)):
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


