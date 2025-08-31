from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class Unit:
    x: int
    y: int
    owner: str
    symbol: str
    max_hp: int = 10
    hp: int = 10
    movement_points: int = 1
    fuel: Optional[int] = None  # For aircraft
    moves_left: int = 1  # reset each turn
    # For support cap logic: which city supports this unit (if any)
    home_city: Optional[Tuple[int, int]] = None

    def is_alive(self) -> bool:
        return self.hp > 0

    def reset_moves(self) -> None:
        self.moves_left = self.movement_points

    def can_move(self) -> bool:
        return self.moves_left > 0 and self.is_alive()


class Army(Unit):
    def __init__(self, x: int, y: int, owner: str) -> None:
        super().__init__(x=x, y=y, owner=owner, symbol='A', max_hp=10, hp=10, movement_points=1)


# Placeholders for future units (Sprint 2)
class Destroyer(Unit):
    def __init__(self, x: int, y: int, owner: str) -> None:
        super().__init__(x=x, y=y, owner=owner, symbol='D', max_hp=12, hp=12, movement_points=3)


class Fighter(Unit):
    def __init__(self, x: int, y: int, owner: str) -> None:
        super().__init__(x=x, y=y, owner=owner, symbol='F', max_hp=8, hp=8, movement_points=6, fuel=8)


