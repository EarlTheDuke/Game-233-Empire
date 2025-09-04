from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Dict, List, Tuple

from map import City, GameMap
from units import Unit


def serialize_map(game_map: GameMap) -> Dict[str, Any]:
    return {
        "width": game_map.width,
        "height": game_map.height,
        "tiles": game_map.tiles,
        "fog": game_map.fog,
        "cities": [asdict(c) for c in game_map.cities],
        "explored": game_map.explored,
    }


def deserialize_map(data: Dict[str, Any]) -> GameMap:
    m = GameMap(data["width"], data["height"])
    m.tiles = data["tiles"]
    m.fog = data["fog"]
    m.cities = [City(**c) for c in data["cities"]]
    m.explored = data.get("explored", {})
    return m


def serialize_units(units: List[Unit]) -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for unit in units:
        d = dict(unit.__dict__)
        # include explicit unit type for robust deserialization
        d["unit_type"] = type(unit).__name__
        # Ensure tuples become lists for JSON compatibility where needed
        if isinstance(d.get("home_city"), tuple):
            d["home_city"] = list(d["home_city"])  # type: ignore[index]
        # tuples are JSON-serializable; keep as is
        payload.append(d)
    return payload


def serialize_players(players: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return players


def deserialize_players(players_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return players_data


def save_full_game(path: str, game_map: GameMap, units: List[Unit], players: List[Dict[str, Any]], turn_number: int, current_player: str) -> None:
    payload = {
        "map": serialize_map(game_map),
        "units": serialize_units(units),
        "players": serialize_players(players),
        "turn_number": turn_number,
        "current_player": current_player,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def load_full_game(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


