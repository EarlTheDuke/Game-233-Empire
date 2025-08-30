from __future__ import annotations

import random
from typing import Tuple

from units import Unit


def resolve_attack(attacker: Unit, defender: Unit) -> Tuple[bool, bool]:
    """
    Minimal probabilistic combat model.
    Returns (attacker_alive, defender_alive).

    Notes:
    - Placeholder logic inspired by dice-roll loops mentioned in classic Empire.
    - To be expanded in Sprint 4 with matchup tables and city conquest rules.
    """
    rng = random.Random()

    # Base hit chances
    attacker_hit = 0.55
    defender_hit = 0.50

    # Simple exchange of blows until one drops
    while attacker.hp > 0 and defender.hp > 0:
        if rng.random() < attacker_hit:
            defender.hp -= 3
        if defender.hp <= 0:
            break
        if rng.random() < defender_hit:
            attacker.hp -= 2

    return attacker.hp > 0, defender.hp > 0


