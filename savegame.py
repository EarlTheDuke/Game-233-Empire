from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Dict, List

from map import City, GameMap
from units import Unit


def serialize_map(game_map: GameMap) -> Dict[str, Any]:
    return {
        "width": game_map.width,
        "height": game_map.height,
        "tiles": game_map.tiles,
        "fog": game_map.fog,
        "cities": [asdict(c) for c in game_map.cities],
    }


def deserialize_map(data: Dict[str, Any]) -> GameMap:
    m = GameMap(data["width"], data["height"])
    m.tiles = data["tiles"]
    m.fog = data["fog"]
    m.cities = [City(**c) for c in data["cities"]]
    return m


def serialize_units(units: List[Unit]) -> List[Dict[str, Any]]:
    return [unit.__dict__ for unit in units]


def save_game(path: str, game_map: GameMap, units: List[Unit]) -> None:
    payload = {
        "map": serialize_map(game_map),
        "units": serialize_units(units),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def load_game(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


