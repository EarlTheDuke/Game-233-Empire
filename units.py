from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


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

    def is_alive(self) -> bool:
        return self.hp > 0


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


