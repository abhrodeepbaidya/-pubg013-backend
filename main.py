from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

app = FastAPI(title="Battle Royale Backend", version="4.0.0")
DB_PATH = Path(__file__).resolve().parent / "game.db"

BASE_CAPACITY = 20
BACKPACK_CAPACITY = {0: 0, 1: 150, 2: 200, 3: 250}
VEST_CAPACITY = {0: 0, 1: 50, 2: 100, 3: 150}

# weapon_id: (category, ammo_type, slot_type)
WEAPONS = {
    "M416": ("AR", "5.56mm", "primary"),
    "AKM": ("AR", "7.62mm", "primary"),
    "SCAR-L": ("AR", "5.56mm", "primary"),
    "M16A4": ("AR", "5.56mm", "primary"),
    "UMP45": ("SMG", ".45 ACP", "primary"),
    "UZI": ("SMG", "9mm", "primary"),
    "Vector": ("SMG", ".45 ACP", "primary"),
    "Kar98k": ("Sniper", "7.62mm", "primary"),
    "M24": ("Sniper", "7.62mm", "primary"),
    "AWM": ("Sniper", ".300 Magnum", "primary"),
    "Mini14": ("DMR", "5.56mm", "primary"),
    "SKS": ("DMR", "7.62mm", "primary"),
    "DP-28": ("LMG", "7.62mm", "primary"),
    "M249": ("LMG", "5.56mm", "primary"),
    "S1897": ("Shotgun", "12 Gauge", "primary"),
    "S686": ("Shotgun", "12 Gauge", "primary"),
    "P92": ("Pistol", "9mm", "pistol"),
    "P1911": ("Pistol", ".45 ACP", "pistol"),
    "R1895": ("Pistol", "7.62mm", "pistol"),
}

# attachment_id: (slot type, compatible weapons)
ATTACHMENTS = {
    "red_dot": ("optic", {"M416", "AKM", "SCAR-L", "M16A4", "UMP45", "UZI", "Vector", "Mini14", "SKS", "DP-28", "M249"}),
    "holographic": ("optic", {"M416", "AKM", "SCAR-L", "M16A4", "UMP45", "UZI", "Vector", "Mini14", "SKS", "DP-28", "M249"}),
    "scope_2x": ("optic", {"M416", "AKM", "SCAR-L", "M16A4", "UMP45", "UZI", "Vector", "Mini14", "SKS"}),
    "scope_3x": ("optic", {"M416", "SCAR-L", "M16A4", "Mini14", "SKS"}),
    "scope_4x": ("optic", {"M416", "AKM", "SCAR-L", "M16A4", "Kar98k", "M24", "AWM", "Mini14", "SKS"}),
    "scope_6x": ("optic", {"M416", "SCAR-L", "M16A4", "Kar98k", "M24", "AWM", "Mini14", "SKS"}),
    "scope_8x": ("optic", {"Kar98k", "M24", "AWM", "Mini14", "SKS"}),
    "compensator": ("muzzle", {"M416", "AKM", "SCAR-L", "M16A4", "UMP45", "UZI", "Vector", "Mini14", "SKS"}),
    "suppressor": ("muzzle", {"M416", "AKM", "SCAR-L", "M16A4", "UMP45", "UZI", "Vector", "Kar98k", "M24", "AWM", "Mini14", "SKS", "P92", "P1911"}),
    "vertical_grip": ("grip", {"M416", "SCAR-L", "M16A4", "UMP45", "Vector"}),
    "angled_grip": ("grip", {"M416", "SCAR-L", "M16A4", "UMP45", "Vector"}),
    "extended_mag": ("magazine", {"M416", "AKM", "SCAR-L", "M16A4", "UMP45", "UZI", "Vector", "P92", "P1911"}),
    "quickdraw_mag": ("magazine", {"M416", "AKM", "SCAR-L", "M16A4", "UMP45", "UZI", "Vector", "P92", "P1911"}),
}

ITEM_WEIGHTS = {
    "5.56mm": 1, "7.62mm": 1, "9mm": 1, ".45 ACP": 1,
    ".300 Magnum": 1, "12 Gauge": 1, "bandage": 2,
    "first_aid_kit": 10, "med_kit": 20, "energy_drink": 4,
    "painkiller": 10, "frag_grenade": 12, "smoke_grenade": 10,
    "molotov": 10,
}

# Base magazine sizes for this prototype. Extended magazines add capacity.
MAGAZINE_CAPACITY = {
    "M416": 30, "AKM": 30, "SCAR-L": 30, "M16A4": 30,
    "UMP45": 25, "UZI": 25, "Vector": 19,
    "Kar98k": 5, "M24": 5, "AWM": 5,
    "Mini14": 20, "SKS": 10, "DP-28": 47, "M249": 100,
    "S1897": 5, "S686": 2,
    "P92": 15, "P1911": 7, "R1895": 7,
}
EXTENDED_MAG_BONUS = 10


def weapon_magazine_capacity(weapon: dict) -> int:
    base = MAGAZINE_CAPACITY.get(weapon["weapon_id"], 30)
    if "extended_mag" in weapon.get("attachments", []):
        base += EXTENDED_MAG_BONUS
    return base


def db():
    return sqlite3.connect(DB_PATH)


def init_db():
    with db() as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS players "
            "(player_id TEXT PRIMARY KEY, state_json TEXT NOT NULL)"
        )


def fresh_player():
    return {
        "weapons": {"primary_1": None, "primary_2": None, "pistol": None},
        "melee": "Pan",
        "active_throwable": None,
        "items": [],
        "backpack_level": 0,
        "vest_level": 0,
        "health": 100,
        "boost": 0,
        "armor": {"helmet_level": 0, "helmet_durability": 0, "vest_durability": 0},
        "throwables": {"frag_grenade": 0, "smoke_grenade": 0, "molotov": 0},
        "vehicle": None,
        "match": {"status": "idle", "match_id": None},
    }


def save_player(player_id: str, state: dict):
    with db() as con:
        con.execute(
            "INSERT INTO players(player_id,state_json) VALUES(?,?) "
            "ON CONFLICT(player_id) DO UPDATE SET state_json=excluded.state_json",
            (player_id, json.dumps(state)),
        )


def get_player(player_id: str):
    with db() as con:
        row = con.execute(
            "SELECT state_json FROM players WHERE player_id=?", (player_id,)
        ).fetchone()
    if row is None:
        state = fresh_player()
        save_player(player_id, state)
        return state
    return json.loads(row[0])


def used_capacity(state: dict) -> int:
    return sum(i["quantity"] * i["weight"] for i in state["items"])


def max_capacity(state: dict) -> int:
    return BASE_CAPACITY + BACKPACK_CAPACITY[state["backpack_level"]] + VEST_CAPACITY[state["vest_level"]]


def require_weapon(state: dict, slot: str):
    weapon = state["weapons"][slot]
    if weapon is None:
        raise HTTPException(400, "No weapon equipped in this slot")
    return weapon


class WeaponInput(BaseModel):
    weapon_id: str
    ammo: int = Field(default=0, ge=0)
    reserve_ammo: int = Field(default=0, ge=0)
    attachments: list[str] = Field(default_factory=list)


class AttachmentInput(BaseModel):
    attachment_id: str


class LevelInput(BaseModel):
    level: int = Field(ge=0, le=3)


class ItemInput(BaseModel):
    item_id: str
    quantity: int = Field(ge=1, le=10000)
    weight: int | None = Field(default=None, ge=0)


@app.on_event("startup")
def startup():
    init_db()


@app.get("/")
def home():
    return {"server": "online", "project": "Battle Royale Backend", "version": app.version}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/players/{player_id}")
def create_player(player_id: str):
    if not player_id or len(player_id) > 64:
        raise HTTPException(400, "Invalid player_id")
    with db() as con:
        exists = con.execute("SELECT 1 FROM players WHERE player_id=?", (player_id,)).fetchone()
    if exists:
        raise HTTPException(409, "Player already exists")
    state = fresh_player()
    save_player(player_id, state)
    return {"status": "created", "player_id": player_id, "player": state}


@app.get("/players")
def list_players():
    with db() as con:
        rows = con.execute("SELECT player_id FROM players ORDER BY player_id").fetchall()
    return {"players": [r[0] for r in rows]}


@app.get("/player/{player_id}/inventory")
def inventory(player_id: str):
    state = get_player(player_id)
    used, maximum = used_capacity(state), max_capacity(state)
    return {
        "player_id": player_id, **state,
        "used_capacity": used, "max_capacity": maximum,
        "remaining_capacity": maximum - used,
    }


@app.get("/weapons/catalog")
def weapon_catalog():
    return {
        "weapons": [
            {"weapon_id": n, "category": v[0], "ammo_type": v[1], "slot": v[2]}
            for n, v in WEAPONS.items()
        ],
        "attachments": [
            {"attachment_id": n, "type": v[0], "compatible_weapons": sorted(v[1])}
            for n, v in ATTACHMENTS.items()
        ],
    }


@app.post("/player/{player_id}/weapon/{slot}")
def equip_weapon(
    player_id: str,
    slot: Literal["primary_1", "primary_2", "pistol"],
    data: WeaponInput,
):
    if data.weapon_id not in WEAPONS:
        raise HTTPException(400, "Unknown weapon")
    category, ammo_type, allowed_slot = WEAPONS[data.weapon_id]
    if allowed_slot == "pistol" and slot != "pistol":
        raise HTTPException(400, "Pistol must use pistol slot")
    if allowed_slot == "primary" and slot == "pistol":
        raise HTTPException(400, "Primary weapon cannot use pistol slot")
    seen_types = set()
    for attachment in data.attachments:
        if attachment not in ATTACHMENTS:
            raise HTTPException(400, f"Unknown attachment: {attachment}")
        kind, compatible = ATTACHMENTS[attachment]
        if data.weapon_id not in compatible:
            raise HTTPException(400, f"{attachment} incompatible with {data.weapon_id}")
        if kind in seen_types:
            raise HTTPException(400, f"Only one {kind} can be equipped")
        seen_types.add(kind)
    state = get_player(player_id)
    state["weapons"][slot] = {
        "weapon_id": data.weapon_id, "category": category,
        "ammo_type": ammo_type, "ammo": data.ammo,
        "reserve_ammo": data.reserve_ammo,
        "attachments": data.attachments,
        "magazine_capacity": MAGAZINE_CAPACITY.get(data.weapon_id, 30)
            + (EXTENDED_MAG_BONUS if "extended_mag" in data.attachments else 0),
    }
    save_player(player_id, state)
    return {"status": "equipped", "slot": slot, "weapon": state["weapons"][slot]}


@app.delete("/player/{player_id}/weapon/{slot}")
def remove_weapon(player_id: str, slot: Literal["primary_1", "primary_2", "pistol"]):
    state = get_player(player_id)
    if state["weapons"][slot] is None:
        raise HTTPException(404, "No weapon in slot")
    removed = state["weapons"][slot]
    state["weapons"][slot] = None
    save_player(player_id, state)
    return {"status": "removed", "weapon": removed}


@app.post("/player/{player_id}/weapon/{slot}/attachment")
def add_attachment(
    player_id: str,
    slot: Literal["primary_1", "primary_2", "pistol"],
    data: AttachmentInput,
):
    state = get_player(player_id)
    weapon = require_weapon(state, slot)
    if data.attachment_id not in ATTACHMENTS:
        raise HTTPException(400, "Unknown attachment")
    kind, compatible = ATTACHMENTS[data.attachment_id]
    if weapon["weapon_id"] not in compatible:
        raise HTTPException(400, "Attachment incompatible with weapon")
    if any(ATTACHMENTS[a][0] == kind for a in weapon["attachments"]):
        raise HTTPException(400, f"An attachment of type {kind} is already equipped")
    weapon["attachments"].append(data.attachment_id)
    weapon["magazine_capacity"] = weapon_magazine_capacity(weapon)
    save_player(player_id, state)
    return {"status": "attachment_equipped", "weapon": weapon["weapon_id"], "attachments": weapon["attachments"]}


@app.delete("/player/{player_id}/weapon/{slot}/attachment/{attachment_id}")
def remove_attachment(player_id: str, slot: Literal["primary_1", "primary_2", "pistol"], attachment_id: str):
    state = get_player(player_id)
    weapon = require_weapon(state, slot)
    if attachment_id not in weapon["attachments"]:
        raise HTTPException(404, "Attachment not equipped")
    weapon["attachments"].remove(attachment_id)
    weapon["magazine_capacity"] = weapon_magazine_capacity(weapon)
    save_player(player_id, state)
    return {"status": "attachment_removed", "attachments": weapon["attachments"]}


@app.put("/player/{player_id}/backpack")
def backpack(player_id: str, data: LevelInput):
    state = get_player(player_id)
    state["backpack_level"] = data.level
    save_player(player_id, state)
    return {"status": "equipped", "level": data.level, "capacity": max_capacity(state)}


@app.put("/player/{player_id}/vest")
def vest(player_id: str, data: LevelInput):
    state = get_player(player_id)
    state["vest_level"] = data.level
    save_player(player_id, state)
    return {"status": "equipped", "level": data.level, "capacity": max_capacity(state)}


@app.post("/player/{player_id}/item")
def add_item(player_id: str, data: ItemInput):
    state = get_player(player_id)
    weight = data.weight if data.weight is not None else ITEM_WEIGHTS.get(data.item_id, 1)
    if used_capacity(state) + data.quantity * weight > max_capacity(state):
        raise HTTPException(400, "Inventory capacity exceeded")
    item = next((x for x in state["items"] if x["item_id"] == data.item_id and x["weight"] == weight), None)
    if item:
        item["quantity"] += data.quantity
    else:
        state["items"].append({"item_id": data.item_id, "quantity": data.quantity, "weight": weight})
    save_player(player_id, state)
    return {"status": "item_added", "item_id": data.item_id, "quantity": data.quantity}


@app.delete("/player/{player_id}/item/{item_id}")
def drop_item(player_id: str, item_id: str, quantity: int = Query(default=1, ge=1)):
    state = get_player(player_id)
    item = next((x for x in state["items"] if x["item_id"] == item_id), None)
    if item is None:
        raise HTTPException(404, "Item not found")
    if item["quantity"] < quantity:
        raise HTTPException(400, "Not enough quantity")
    item["quantity"] -= quantity
    if item["quantity"] == 0:
        state["items"].remove(item)
    save_player(player_id, state)
    return {"status": "dropped", "item_id": item_id, "quantity": quantity}


@app.put("/player/{player_id}/health")
def set_health(player_id: str, health: int = Query(ge=0, le=100)):
    state = get_player(player_id)
    state["health"] = health
    save_player(player_id, state)
    return {"player_id": player_id, "health": health}


@app.get("/player/{player_id}/capacity")
def capacity(player_id: str):
    state = get_player(player_id)
    used, maximum = used_capacity(state), max_capacity(state)
    return {"used": used, "maximum": maximum, "remaining": maximum - used}


@app.delete("/player/{player_id}")
def delete_player(player_id: str):
    with db() as con:
        cur = con.execute("DELETE FROM players WHERE player_id=?", (player_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Player not found")
    return {"status": "deleted", "player_id": player_id}


@app.get("/player/{player_id}/weapon/{slot}/ammo")
def weapon_ammo(player_id: str, slot: Literal["primary_1", "primary_2", "pistol"]):
    state = get_player(player_id)
    weapon = require_weapon(state, slot)
    capacity = weapon.get("magazine_capacity", weapon_magazine_capacity(weapon))
    return {
        "player_id": player_id,
        "slot": slot,
        "weapon_id": weapon["weapon_id"],
        "ammo_in_magazine": weapon["ammo"],
        "magazine_capacity": capacity,
        "reserve_ammo": weapon["reserve_ammo"],
        "total_ammo": weapon["ammo"] + weapon["reserve_ammo"],
    }


@app.post("/player/{player_id}/weapon/{slot}/reload")
def reload_weapon(player_id: str, slot: Literal["primary_1", "primary_2", "pistol"]):
    state = get_player(player_id)
    weapon = require_weapon(state, slot)
    capacity = weapon.get("magazine_capacity", weapon_magazine_capacity(weapon))
    current = weapon["ammo"]
    reserve = weapon["reserve_ammo"]
    needed = max(0, capacity - current)

    if needed == 0:
        raise HTTPException(400, "Magazine is already full")
    if reserve <= 0:
        raise HTTPException(400, "No reserve ammo available")

    loaded = min(needed, reserve)
    weapon["ammo"] = current + loaded
    weapon["reserve_ammo"] = reserve - loaded
    save_player(player_id, state)
    return {
        "status": "reloaded",
        "weapon_id": weapon["weapon_id"],
        "loaded": loaded,
        "ammo_in_magazine": weapon["ammo"],
        "magazine_capacity": capacity,
        "reserve_ammo": weapon["reserve_ammo"],
    }


@app.post("/player/{player_id}/weapon/{slot}/fire")
def fire_weapon(
    player_id: str,
    slot: Literal["primary_1", "primary_2", "pistol"],
    shots: int = Query(default=1, ge=1, le=10),
):
    state = get_player(player_id)
    weapon = require_weapon(state, slot)
    if weapon["ammo"] < shots:
        raise HTTPException(400, "Not enough ammo in magazine")

    weapon["ammo"] -= shots
    save_player(player_id, state)
    return {
        "status": "fired",
        "weapon_id": weapon["weapon_id"],
        "shots_fired": shots,
        "ammo_in_magazine": weapon["ammo"],
        "reserve_ammo": weapon["reserve_ammo"],
    }


# Initialize database when module is imported by Uvicorn.
init_db()

# --- Version 4 gameplay systems: healing, boost, armor, loot, vehicles and match flow ---

class HealInput(BaseModel):
    item_id: Literal["bandage", "first_aid_kit", "med_kit"]
    quantity: int = Field(default=1, ge=1, le=10)


class DamageInput(BaseModel):
    damage: int = Field(ge=1, le=200)
    hit_zone: Literal["head", "body", "limbs"] = "body"


class ArmorInput(BaseModel):
    helmet_level: int = Field(default=0, ge=0, le=3)
    vest_level: int = Field(default=0, ge=0, le=3)


class BoostInput(BaseModel):
    item_id: Literal["energy_drink", "painkiller", "adrenaline_syringe"]
    quantity: int = Field(default=1, ge=1, le=10)


class ThrowableInput(BaseModel):
    item_id: Literal["frag_grenade", "smoke_grenade", "molotov"]
    quantity: int = Field(default=1, ge=1, le=10)


class VehicleInput(BaseModel):
    vehicle_id: str = Field(min_length=1, max_length=40)
    fuel: int = Field(default=100, ge=0, le=100)


class LootInput(BaseModel):
    loot_id: str = Field(min_length=1, max_length=64)
    item_id: str = Field(min_length=1, max_length=64)
    quantity: int = Field(ge=1, le=1000)
    weight: int = Field(default=1, ge=0, le=1000)


# PUBG-like prototype tuning values; these are game-design parameters, not official server data.
HEAL_VALUES = {"bandage": 15, "first_aid_kit": 75, "med_kit": 100}
BOOST_VALUES = {"energy_drink": 40, "painkiller": 60, "adrenaline_syringe": 100}
VEST_REDUCTION = {0: 0.0, 1: 0.30, 2: 0.40, 3: 0.55}
HELMET_REDUCTION = {0: 0.0, 1: 0.30, 2: 0.40, 3: 0.55}
HELMET_DURABILITY = {0: 0, 1: 80, 2: 150, 3: 230}
VEST_DURABILITY = {0: 0, 1: 200, 2: 220, 3: 250}
LOOT_DROPS = {}


def ensure_v4_state(state: dict):
    state.setdefault("armor", {"helmet_level": 0, "helmet_durability": 0, "vest_durability": 0})
    state.setdefault("throwables", {"frag_grenade": 0, "smoke_grenade": 0, "molotov": 0})
    state.setdefault("vehicle", None)
    state.setdefault("match", {"status": "idle", "match_id": None})
    state.setdefault("boost", 0)
    return state


def consume_item(state: dict, item_id: str, quantity: int = 1):
    item = next((x for x in state["items"] if x["item_id"] == item_id), None)
    if item is None or item["quantity"] < quantity:
        raise HTTPException(400, f"Not enough {item_id}")
    item["quantity"] -= quantity
    if item["quantity"] == 0:
        state["items"].remove(item)


@app.post("/player/{player_id}/heal")
def heal_player(player_id: str, data: HealInput):
    state = ensure_v4_state(get_player(player_id))
    if data.item_id == "bandage" and state["health"] >= 75:
        raise HTTPException(400, "Bandages cannot heal above 75 health")
    if data.item_id == "first_aid_kit" and state["health"] >= 75:
        raise HTTPException(400, "Health is already at or above 75")
    if state["health"] >= 100:
        raise HTTPException(400, "Health is already full")
    consume_item(state, data.item_id, data.quantity)
    amount = HEAL_VALUES[data.item_id] * data.quantity
    if data.item_id == "first_aid_kit":
        state["health"] = min(75, state["health"] + amount)
    else:
        state["health"] = min(100, state["health"] + amount)
    save_player(player_id, state)
    return {"status": "healed", "item_id": data.item_id, "health": state["health"]}


@app.post("/player/{player_id}/boost")
def use_boost(player_id: str, data: BoostInput):
    state = ensure_v4_state(get_player(player_id))
    consume_item(state, data.item_id, data.quantity)
    state["boost"] = min(100, state["boost"] + BOOST_VALUES[data.item_id] * data.quantity)
    save_player(player_id, state)
    return {"status": "boost_used", "boost": state["boost"]}


@app.put("/player/{player_id}/armor")
def equip_armor(player_id: str, data: ArmorInput):
    state = ensure_v4_state(get_player(player_id))
    state["armor"] = {
        "helmet_level": data.helmet_level,
        "helmet_durability": HELMET_DURABILITY[data.helmet_level],
        "vest_durability": VEST_DURABILITY[state["vest_level"]],
    }
    state["vest_level"] = data.vest_level
    state["armor"]["vest_durability"] = VEST_DURABILITY[data.vest_level]
    save_player(player_id, state)
    return {"status": "armor_equipped", "armor": state["armor"]}


@app.post("/player/{player_id}/damage")
def apply_damage(player_id: str, data: DamageInput):
    state = ensure_v4_state(get_player(player_id))
    armor = state["armor"]
    level_key = "helmet_level" if data.hit_zone == "head" else None
    durability_key = "helmet_durability" if data.hit_zone == "head" else "vest_durability"
    armor_level = armor[level_key] if level_key else state["vest_level"]
    durability = armor[durability_key]
    reduction = HELMET_REDUCTION[armor_level] if data.hit_zone == "head" else VEST_REDUCTION[state["vest_level"]]
    absorbed = min(durability, int(data.damage * reduction))
    armor[durability_key] = max(0, durability - absorbed)
    actual_damage = data.damage - absorbed
    state["health"] = max(0, state["health"] - actual_damage)
    save_player(player_id, state)
    return {
        "status": "damage_applied", "damage_received": data.damage,
        "armor_absorbed": absorbed, "health_damage": actual_damage,
        "health": state["health"], "armor": armor,
        "knocked_out": state["health"] == 0,
    }


@app.post("/player/{player_id}/throwable")
def add_throwable(player_id: str, data: ThrowableInput):
    state = ensure_v4_state(get_player(player_id))
    consume_item(state, data.item_id, data.quantity)
    state["throwables"][data.item_id] += data.quantity
    save_player(player_id, state)
    return {"status": "throwable_ready", "throwables": state["throwables"]}


@app.post("/player/{player_id}/throwable/{item_id}/use")
def use_throwable(player_id: str, item_id: Literal["frag_grenade", "smoke_grenade", "molotov"]):
    state = ensure_v4_state(get_player(player_id))
    if state["throwables"][item_id] < 1:
        raise HTTPException(400, "Throwable not available in active slot")
    state["throwables"][item_id] -= 1
    save_player(player_id, state)
    return {"status": "thrown", "item_id": item_id, "remaining": state["throwables"][item_id]}


@app.post("/loot")
def create_loot(data: LootInput):
    if data.loot_id in LOOT_DROPS:
        raise HTTPException(409, "Loot drop already exists")
    LOOT_DROPS[data.loot_id] = {
        "item_id": data.item_id, "quantity": data.quantity, "weight": data.weight
    }
    return {"status": "loot_spawned", "loot_id": data.loot_id, **LOOT_DROPS[data.loot_id]}


@app.post("/player/{player_id}/loot/{loot_id}/pickup")
def pickup_loot(player_id: str, loot_id: str):
    state = ensure_v4_state(get_player(player_id))
    loot = LOOT_DROPS.get(loot_id)
    if loot is None:
        raise HTTPException(404, "Loot not found")
    if used_capacity(state) + loot["quantity"] * loot["weight"] > max_capacity(state):
        raise HTTPException(400, "Inventory capacity exceeded")
    item = next((x for x in state["items"] if x["item_id"] == loot["item_id"] and x["weight"] == loot["weight"]), None)
    if item:
        item["quantity"] += loot["quantity"]
    else:
        state["items"].append({"item_id": loot["item_id"], "quantity": loot["quantity"], "weight": loot["weight"]})
    del LOOT_DROPS[loot_id]
    save_player(player_id, state)
    return {"status": "loot_picked_up", "item_id": loot["item_id"], "quantity": loot["quantity"]}


@app.put("/player/{player_id}/vehicle")
def enter_vehicle(player_id: str, data: VehicleInput):
    state = ensure_v4_state(get_player(player_id))
    if state["vehicle"] is not None:
        raise HTTPException(409, "Player is already in a vehicle")
    state["vehicle"] = {"vehicle_id": data.vehicle_id, "fuel": data.fuel}
    save_player(player_id, state)
    return {"status": "entered_vehicle", "vehicle": state["vehicle"]}


@app.post("/player/{player_id}/vehicle/fuel")
def consume_vehicle_fuel(player_id: str, amount: int = Query(default=1, ge=1, le=100)):
    state = ensure_v4_state(get_player(player_id))
    if state["vehicle"] is None:
        raise HTTPException(400, "Player is not in a vehicle")
    state["vehicle"]["fuel"] = max(0, state["vehicle"]["fuel"] - amount)
    if state["vehicle"]["fuel"] == 0:
        state["vehicle"]["engine"] = "out_of_fuel"
    save_player(player_id, state)
    return {"status": "fuel_updated", "vehicle": state["vehicle"]}


@app.delete("/player/{player_id}/vehicle")
def exit_vehicle(player_id: str):
    state = ensure_v4_state(get_player(player_id))
    if state["vehicle"] is None:
        raise HTTPException(400, "Player is not in a vehicle")
    vehicle = state["vehicle"]
    state["vehicle"] = None
    save_player(player_id, state)
    return {"status": "exited_vehicle", "vehicle": vehicle}


@app.post("/player/{player_id}/match/join")
def join_match(player_id: str, match_id: str = Query(min_length=1, max_length=64)):
    state = ensure_v4_state(get_player(player_id))
    if state["match"]["status"] == "in_match":
        raise HTTPException(409, "Player is already in a match")
    state["match"] = {"status": "in_match", "match_id": match_id}
    save_player(player_id, state)
    return {"status": "match_joined", "match": state["match"]}


@app.post("/player/{player_id}/match/leave")
def leave_match(player_id: str):
    state = ensure_v4_state(get_player(player_id))
    if state["match"]["status"] != "in_match":
        raise HTTPException(400, "Player is not in a match")
    state["match"] = {"status": "idle", "match_id": None}
    save_player(player_id, state)
    return {"status": "match_left", "match": state["match"]}


@app.get("/match/{match_id}/players")
def match_players(match_id: str):
    with db() as con:
        rows = con.execute("SELECT player_id, state_json FROM players").fetchall()
    players = []
    for player_id, raw in rows:
        player_state = json.loads(raw)
        match = player_state.get("match", {"status": "idle", "match_id": None})
        if match.get("status") == "in_match" and match.get("match_id") == match_id:
            players.append(player_id)
    return {"match_id": match_id, "players": players, "count": len(players)}


@app.get("/systems")
def systems_status():
    return {
        "version": app.version,
        "systems": {
            "players_inventory": "available",
            "weapons_attachments_ammo": "available",
            "healing_boost": "available",
            "armor_damage": "prototype",
            "throwables": "prototype",
            "loot_drops": "prototype_in_memory",
            "vehicles_fuel": "prototype",
            "match_join_leave": "prototype_no_matchmaking",
        },
    }
