from __future__ import annotations

import random
from typing import Tuple

from units import Unit


def resolve_attack(attacker: Unit, defender: Unit, attacker_hit: float = 0.55, defender_hit: float = 0.50) -> Tuple[bool, bool]:
    """
    Minimal probabilistic combat model.
    Returns (attacker_alive, defender_alive).

    Notes:
    - Placeholder logic inspired by dice-roll loops mentioned in classic Empire.
    - To be expanded in Sprint 4 with matchup tables and city conquest rules.
    """
    rng = random.Random()

    # Hit chances can be modified by caller (e.g., city defense bonus)
    attacker_hit = max(0.20, min(0.80, attacker_hit))
    defender_hit = max(0.20, min(0.80, defender_hit))

    # Simple exchange of blows until one drops
    while attacker.hp > 0 and defender.hp > 0:
        if rng.random() < attacker_hit:
            defender.hp -= 3
        if defender.hp <= 0:
            break
        if rng.random() < defender_hit:
            attacker.hp -= 2

    return attacker.hp > 0, defender.hp > 0


