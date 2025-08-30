from __future__ import annotations

from dataclasses import dataclass, field
from typing import Set, Tuple


@dataclass
class Player:
    name: str
    is_ai: bool = False
    cities: Set[Tuple[int, int]] = field(default_factory=set)


class AIController:
    def __init__(self, name: str) -> None:
        self.name = name

    def take_turn(self) -> None:
        # Placeholder for Sprint 5: choose targets, move units, set production
        pass


