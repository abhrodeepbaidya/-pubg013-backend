from __future__ import annotations

import json
import sqlite3
import secrets
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

app = FastAPI(title="Battle Royale Backend", version="8.0.0")
DB_PATH = Path(__file__).resolve().parent / "game.db"

BASE_CAPACITY = 20
BACKPACK_CAPACITY = {0: 0, 1: 150, 2: 200, 3: 250}
VEST_CAPACITY = {0: 0, 1: 50, 2: 100, 3: 150}

# PUBG Mobile classic-era weapons: (category, ammo_type, slot_type)
WEAPONS = {
 "M416":("AR","5.56mm","primary"), "AKM":("AR","7.62mm","primary"), "SCAR-L":("AR","5.56mm","primary"), "M16A4":("AR","5.56mm","primary"),
 "AUG A3":("AR","5.56mm","primary"), "Groza":("AR","7.62mm","primary"), "QBZ":("AR","5.56mm","primary"),
 "UMP9":("SMG","9mm","primary"), "UMP45":("SMG",".45 ACP","primary"), "UZI":("SMG","9mm","primary"), "Vector":("SMG",".45 ACP","primary"), "Tommy Gun":("SMG",".45 ACP","primary"),
 "Kar98k":("Sniper","7.62mm","primary"), "M24":("Sniper","7.62mm","primary"), "AWM":("Sniper",".300 Magnum","primary"), "Win94":("Sniper",".45 ACP","primary"),
 "Mini14":("DMR","5.56mm","primary"), "SKS":("DMR","7.62mm","primary"), "Mk14":("DMR","7.62mm","primary"), "QBU":("DMR","5.56mm","primary"),
 "DP-28":("LMG","7.62mm","primary"), "M249":("LMG","5.56mm","primary"),
 "S1897":("Shotgun","12 Gauge","primary"), "S686":("Shotgun","12 Gauge","primary"), "S12K":("Shotgun","12 Gauge","primary"),
 "P92":("Pistol","9mm","pistol"), "P18C":("Pistol","9mm","pistol"), "P1911":("Pistol",".45 ACP","pistol"),
 "R1895":("Pistol","7.62mm","pistol"), "R45":("Pistol",".45 ACP","pistol"), "Sawed-off":("Pistol","12 Gauge","pistol"),
 "Crossbow":("Special","Bolt","primary"),
 "Pan":("Melee",None,"melee"), "Machete":("Melee",None,"melee"), "Crowbar":("Melee",None,"melee"), "Sickle":("Melee",None,"melee")
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

# Expand attachment compatibility for the full catalogue while keeping attachment slots distinct.
_ALL_FIREARMS = {name for name, spec in WEAPONS.items() if spec[0] not in {"Melee", "Special"}}
_RIFLES_AND_DMR = {name for name, spec in WEAPONS.items() if spec[0] in {"AR", "DMR", "LMG", "Sniper"}}
_DETACHABLE_MAG_GUNS = {name for name, spec in WEAPONS.items() if spec[0] in {"AR", "SMG", "DMR", "Pistol"}}
for _attachment_id, (_slot_type, _compatible) in list(ATTACHMENTS.items()):
    if _slot_type == "optic":
        ATTACHMENTS[_attachment_id] = (_slot_type, _ALL_FIREARMS - {"Sawed-off"})
    elif _slot_type == "muzzle" and _attachment_id == "compensator":
        ATTACHMENTS[_attachment_id] = (_slot_type, _RIFLES_AND_DMR)
    elif _slot_type == "muzzle" and _attachment_id == "suppressor":
        ATTACHMENTS[_attachment_id] = (_slot_type, _ALL_FIREARMS)
    elif _slot_type == "grip":
        ATTACHMENTS[_attachment_id] = (_slot_type, {n for n in _ALL_FIREARMS if WEAPONS[n][0] in {"AR", "SMG"}})
    elif _slot_type == "magazine":
        ATTACHMENTS[_attachment_id] = (_slot_type, _DETACHABLE_MAG_GUNS)
ATTACHMENTS["extended_quickdraw_mag"] = ("magazine", _DETACHABLE_MAG_GUNS)


ITEM_WEIGHTS = {
    "5.56mm":1,"7.62mm":1,"9mm":1,".45 ACP":1,".300 Magnum":1,"12 Gauge":1,"Bolt":1,"flare":1,
    "pan":20,"machete":10,"crowbar":10,"sickle":10,"bandage":2,
    "first_aid_kit": 10, "med_kit": 20, "energy_drink": 4,
    "painkiller": 10, "frag_grenade": 12, "smoke_grenade": 10,
    "molotov": 10,
}

# Base magazine sizes for this prototype. Extended magazines add capacity.
MAGAZINE_CAPACITY = {
    "M416":30,"AKM":30,"SCAR-L":30,"M16A4":30,"AUG A3":30,"Groza":30,"QBZ":30,
    "UMP9":30,"UMP45":25,"UZI":25,"Vector":19,"Tommy Gun":100,
    "Kar98k":5,"M24":5,"AWM":5,"Win94":8,"Mini14":20,"SKS":10,"Mk14":10,"QBU":10,
    "DP-28":47,"M249":100,"S1897":5,"S686":2,"S12K":5,
    "P92":15,"P18C":17,"P1911":7,"R1895":7,"R45":6,"Sawed-off":2,"Crossbow":1,
}
EXTENDED_MAG_BONUS = 10


def weapon_magazine_capacity(weapon: dict) -> int:
    base = MAGAZINE_CAPACITY.get(weapon["weapon_id"], 30)
    if any(a in weapon.get("attachments", []) for a in ("extended_mag", "extended_quickdraw_mag")):
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
        con.execute(
            "CREATE TABLE IF NOT EXISTS accounts ("
            "player_id TEXT PRIMARY KEY, uid TEXT NOT NULL UNIQUE, "
            "username TEXT NOT NULL, username_key TEXT NOT NULL UNIQUE)"
        )
        con.execute("CREATE TABLE IF NOT EXISTS kill_feed (id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT NOT NULL, killer_id TEXT NOT NULL, victim_id TEXT NOT NULL, weapon_id TEXT NOT NULL, hit_zone TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        con.execute("CREATE TABLE IF NOT EXISTS wallets (player_id TEXT PRIMARY KEY, bp INTEGER NOT NULL DEFAULT 0, uc INTEGER NOT NULL DEFAULT 0, classic_fragments INTEGER NOT NULL DEFAULT 0, premium_fragments INTEGER NOT NULL DEFAULT 0, supply_fragments INTEGER NOT NULL DEFAULT 0)")
        con.execute("CREATE TABLE IF NOT EXISTS owned_cosmetics (id INTEGER PRIMARY KEY AUTOINCREMENT, player_id TEXT NOT NULL, item_id TEXT NOT NULL, crate_id TEXT NOT NULL, rarity TEXT NOT NULL, obtained_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        con.execute("CREATE TABLE IF NOT EXISTS economy_transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, player_id TEXT NOT NULL, currency TEXT NOT NULL, amount INTEGER NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        con.execute("CREATE TABLE IF NOT EXISTS match_rewards (match_id TEXT NOT NULL, player_id TEXT NOT NULL, bp_awarded INTEGER NOT NULL, PRIMARY KEY(match_id, player_id))")


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


class RegisterInput(BaseModel):
    username: str = Field(min_length=3, max_length=16, pattern="^[A-Za-z0-9_]+$")


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


@app.post("/auth/register")
def register_account(data: RegisterInput):
    username = data.username.strip()
    username_key = username.casefold()
    with db() as con:
        if con.execute("SELECT 1 FROM accounts WHERE username_key=?", (username_key,)).fetchone():
            raise HTTPException(409, "Username already in use")
        player_id = str(uuid.uuid4())
        for _ in range(20):
            uid = str(secrets.randbelow(9_000_000_000) + 1_000_000_000)
            try:
                con.execute("INSERT INTO accounts(player_id,uid,username,username_key) VALUES(?,?,?,?)", (player_id, uid, username, username_key))
                break
            except sqlite3.IntegrityError:
                if con.execute("SELECT 1 FROM accounts WHERE username_key=?", (username_key,)).fetchone():
                    raise HTTPException(409, "Username already in use")
        else:
            raise HTTPException(503, "Could not allocate a unique player UID")
    state = fresh_player()
    save_player(player_id, state)
    return {"status":"registered","username":username,"uid":uid,"player_id":player_id,"player":state}


@app.get("/users/{uid}")
def get_account_by_uid(uid: str):
    with db() as con:
        row = con.execute("SELECT uid,username,player_id FROM accounts WHERE uid=?", (uid,)).fetchone()
    if row is None:
        raise HTTPException(404, "Account not found")
    return {"uid":row[0],"username":row[1],"player_id":row[2]}


@app.get("/users/by-username/{username}")
def get_account_by_username(username: str):
    with db() as con:
        row = con.execute("SELECT uid,username,player_id FROM accounts WHERE username_key=?", (username.casefold(),)).fetchone()
    if row is None:
        raise HTTPException(404, "Account not found")
    return {"uid":row[0],"username":row[1],"player_id":row[2]}

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
        "ammo": ["5.56mm", "7.62mm", "9mm", ".45 ACP", ".300 Magnum", "12 Gauge", "Bolt", "flare"],
        "melee": ["Pan", "Machete", "Crowbar", "Sickle"],
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
    if allowed_slot == "melee":
        raise HTTPException(400, "Use the melee equip endpoint for melee weapons")
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
            + (EXTENDED_MAG_BONUS if any(a in data.attachments for a in ("extended_mag", "extended_quickdraw_mag")) else 0),
    }
    save_player(player_id, state)
    return {"status": "equipped", "slot": slot, "weapon": state["weapons"][slot]}


@app.put("/player/{player_id}/melee")
def equip_melee(player_id: str, weapon_id: str = Query(min_length=1)):
    if weapon_id not in WEAPONS or WEAPONS[weapon_id][2] != "melee":
        raise HTTPException(400, "Unknown melee weapon")
    state = get_player(player_id)
    state["melee"] = weapon_id
    save_player(player_id, state)
    return {"status": "melee_equipped", "melee": weapon_id}


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
    state = ensure_v4_state(get_player(player_id))
    state["vest_level"] = data.level
    state["armor"]["vest_durability"] = VEST_DURABILITY[data.level]
    save_player(player_id, state)
    return {
        "status": "vest_equipped",
        "level": data.level,
        "durability": state["armor"]["vest_durability"],
        "capacity": max_capacity(state),
    }


@app.put("/player/{player_id}/helmet")
def helmet(player_id: str, data: LevelInput):
    state = ensure_v4_state(get_player(player_id))
    state["armor"]["helmet_level"] = data.level
    state["armor"]["helmet_durability"] = HELMET_DURABILITY[data.level]
    save_player(player_id, state)
    return {
        "status": "helmet_equipped",
        "level": data.level,
        "durability": state["armor"]["helmet_durability"],
    }


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



# v5 combat and presentation configuration for the UE4 client
WEAPON_DAMAGE = {"M416":41,"AKM":48,"SCAR-L":41,"M16A4":41,"AUG A3":41,"Groza":48,"QBZ":41,"UMP9":35,"UMP45":39,"UZI":26,"Vector":31,"Tommy Gun":40,"Kar98k":75,"M24":79,"AWM":105,"Win94":66,"Mini14":46,"SKS":53,"Mk14":61,"QBU":48,"DP-28":51,"M249":45,"S1897":25,"S686":25,"S12K":22,"P92":35,"P18C":23,"P1911":41,"R1895":55,"R45":55,"Sawed-off":22,"Crossbow":100}
RECOIL_PROFILES = {name: {"vertical":1.0,"horizontal":0.35,"camera_shake":0.25,"recovery":0.8,"hip_spread":1.0} for name in WEAPON_DAMAGE}
for name in ("AKM","Groza"):
    RECOIL_PROFILES[name] = {"vertical":1.45,"horizontal":0.65,"camera_shake":0.45,"recovery":0.55,"hip_spread":1.25}
for name in ("UZI","Vector","P18C"):
    RECOIL_PROFILES[name] = {"vertical":0.65,"horizontal":0.25,"camera_shake":0.18,"recovery":1.1,"hip_spread":0.85}
SCOPE_NAMES = ["hip","red_dot","holographic","2x","3x","4x","6x","8x"]
HIT_MULTIPLIERS = {"head":2.0,"body":1.0,"limbs":0.75}
RANGE_FALLOFF = {"AR":0.0012,"SMG":0.0028,"Sniper":0.00025,"DMR":0.0008,"LMG":0.001,"Pistol":0.002,"Shotgun":0.004,"Special":0.0002}

class CombatHitInput(BaseModel):
    match_id: str = Field(min_length=1,max_length=64)
    shooter_id: str = Field(min_length=1,max_length=64)
    target_id: str = Field(min_length=1,max_length=64)
    weapon_id: str
    hit_zone: Literal["head","body","limbs"] = "body"
    distance_m: float = Field(default=10,ge=0,le=2000)
    shots: int = Field(default=1,ge=1,le=10)

class MovementInput(BaseModel):
    stance: Literal["standing","crouching","prone","swimming"] = "standing"
    is_sprinting: bool = False
    is_jumping: bool = False
    is_climbing: bool = False

class AimInput(BaseModel):
    scope: Literal["hip","red_dot","holographic","2x","3x","4x","6x","8x"] = "hip"
    is_ads: bool = False
    sensitivity: int = Field(default=100,ge=1,le=400)
    gyro_sensitivity: int = Field(default=100,ge=0,le=400)

@app.get("/combat/config")
def combat_config():
    return {"version":app.version,"weapon_damage":WEAPON_DAMAGE,"recoil_profiles":RECOIL_PROFILES,"hit_multipliers":HIT_MULTIPLIERS,"scopes":SCOPE_NAMES,"range_falloff":RANGE_FALLOFF,"client_effects":["camera_shake","muzzle_flash","bullet_tracer","hit_marker","red_hit_effect","blood_particle","kill_notification"]}

@app.get("/weapons/{weapon_id}/handling")
def weapon_handling(weapon_id: str):
    if weapon_id not in WEAPON_DAMAGE:
        raise HTTPException(404,"Weapon profile not found")
    return {"weapon_id":weapon_id,"base_damage":WEAPON_DAMAGE[weapon_id],"recoil":RECOIL_PROFILES[weapon_id],"ammo_type":WEAPONS[weapon_id][1],"magazine_capacity":MAGAZINE_CAPACITY.get(weapon_id,1),"scopes":SCOPE_NAMES}

@app.put("/player/{player_id}/movement")
def update_movement(player_id: str,data: MovementInput):
    state = ensure_v4_state(get_player(player_id))
    state["movement"] = data.model_dump()
    save_player(player_id,state)
    return {"status":"movement_updated","movement":state["movement"]}

@app.put("/player/{player_id}/aim")
def update_aim(player_id: str,data: AimInput):
    state = ensure_v4_state(get_player(player_id))
    state["aim"] = data.model_dump()
    save_player(player_id,state)
    return {"status":"aim_updated","aim":state["aim"]}

@app.post("/combat/hit")
def register_combat_hit(data: CombatHitInput):
    if data.weapon_id not in WEAPON_DAMAGE:
        raise HTTPException(400,"Unknown weapon damage profile")
    if data.shooter_id == data.target_id:
        raise HTTPException(400,"Self damage is not allowed")
    target = ensure_v4_state(get_player(data.target_id))
    if target["health"] <= 0:
        raise HTTPException(409,"Target is already eliminated")
    category = WEAPONS[data.weapon_id][0]
    raw_damage = WEAPON_DAMAGE[data.weapon_id] * data.shots * HIT_MULTIPLIERS[data.hit_zone]
    falloff = max(0.25,1.0-data.distance_m*RANGE_FALLOFF.get(category,0.001))
    damage = max(1,int(raw_damage*falloff))
    armor = target["armor"]
    if data.hit_zone == "head":
        armor_level = armor.get("helmet_level",0)
        durability_key = "helmet_durability"
        reduction = HELMET_REDUCTION.get(armor_level,0)
    else:
        armor_level = target.get("vest_level",0)
        durability_key = "vest_durability"
        reduction = VEST_REDUCTION.get(armor_level,0) if data.hit_zone == "body" else 0
    absorbed = min(armor.get(durability_key,0),int(damage*reduction))
    armor[durability_key] = max(0,armor.get(durability_key,0)-absorbed)
    health_damage = max(0,damage-absorbed)
    target["health"] = max(0,target["health"]-health_damage)
    eliminated = target["health"] == 0
    save_player(data.target_id,target)
    if eliminated:
        with db() as con:
            con.execute("INSERT INTO kill_feed(match_id,killer_id,victim_id,weapon_id,hit_zone) VALUES(?,?,?,?,?)",(data.match_id,data.shooter_id,data.target_id,data.weapon_id,data.hit_zone))
    return {"status":"hit_registered","damage":damage,"armor_absorbed":absorbed,"health_damage":health_damage,"target_health":target["health"],"eliminated":eliminated,"effects":{"hit_marker":True,"red_hit_effect":True,"blood_particle":True,"kill_notification":eliminated}}

@app.get("/match/{match_id}/kill-feed")
def get_kill_feed(match_id: str,limit: int = Query(default=20,ge=1,le=100)):
    with db() as con:
        rows = con.execute("SELECT killer_id,victim_id,weapon_id,hit_zone,created_at FROM kill_feed WHERE match_id=? ORDER BY id DESC LIMIT ?",(match_id,limit)).fetchall()
    return {"match_id":match_id,"events":[{"killer_id":r[0],"victim_id":r[1],"weapon_id":r[2],"hit_zone":r[3],"created_at":r[4]} for r in rows]}

@app.get("/systems")
def systems_status():
    return {
        "version": app.version,
        "systems": {
            "players_inventory": "available",
            "weapons_attachments_ammo": "expanded_classic_catalogue",
            "combat_recoil_aim": "prototype_config_for_ue4_client",
            "hit_detection_kill_feed": "prototype_server_api",
            "healing_boost": "available",
            "armor_damage": "prototype",
            "throwables": "prototype",
            "loot_drops": "prototype_in_memory",
            "vehicles_fuel": "prototype",
            "match_join_leave": "prototype_no_matchmaking",
            "BP_UC_crates_coupons": "prototype_wallet_crates_and_cosmetic_collection",
            "erangel_spawn_timer_zone_results": "classic_timing_prototype_for_ue4_client",
            "throwable_pin_cook_cancel_audio": "prototype_action_api_for_ue4_client",
        },
    }


# v6 economy: BP/UC wallets, crate coupons, fragments and cosmetic inventory.
CRATE_CONFIG = {
    "classic": {
        "currency": "uc", "price": 60, "fragment_key": "classic_fragments",
        "coupon_fragments": 10,
        "rewards": [
            {"item_id":"classic_outfit_common","rarity":"common","weight":55},
            {"item_id":"classic_headgear","rarity":"rare","weight":25},
            {"item_id":"classic_weapon_finish","rarity":"epic","weight":15},
            {"item_id":"classic_legendary_set","rarity":"legendary","weight":5},
        ],
    },
    "premium": {
        "currency": "uc", "price": 60, "fragment_key": "premium_fragments",
        "coupon_fragments": 10,
        "rewards": [
            {"item_id":"premium_outfit_common","rarity":"common","weight":45},
            {"item_id":"premium_headgear","rarity":"rare","weight":30},
            {"item_id":"premium_weapon_finish","rarity":"epic","weight":20},
            {"item_id":"premium_legendary_set","rarity":"legendary","weight":5},
        ],
    },
    "supply": {
        "currency": "bp", "price": 700, "fragment_key": "supply_fragments",
        "coupon_fragments": 10,
        "rewards": [
            {"item_id":"supply_outfit","rarity":"common","weight":65},
            {"item_id":"supply_headgear","rarity":"rare","weight":25},
            {"item_id":"supply_weapon_finish","rarity":"epic","weight":10},
        ],
    },
}

def ensure_wallet(player_id: str):
    with db() as con:
        con.execute("INSERT OR IGNORE INTO wallets(player_id) VALUES(?)", (player_id,))
        row = con.execute("SELECT bp,uc,classic_fragments,premium_fragments,supply_fragments FROM wallets WHERE player_id=?", (player_id,)).fetchone()
    return {"bp":row[0],"uc":row[1],"classic_fragments":row[2],"premium_fragments":row[3],"supply_fragments":row[4]}

def weighted_crate_reward(crate_id: str):
    rewards = CRATE_CONFIG[crate_id]["rewards"]
    total = sum(x["weight"] for x in rewards)
    roll = secrets.randbelow(total)
    for reward in rewards:
        if roll < reward["weight"]:
            return reward
        roll -= reward["weight"]
    return rewards[-1]

@app.get("/economy/config")
def economy_config():
    return {
        "currencies": {"BP":"earned gameplay currency","UC":"premium currency; credited only by trusted purchase validation"},
        "crates": {
            key: {"price":value["price"],"currency":value["currency"],"coupon_fragments":value["coupon_fragments"],
                  "drop_rates_percent":{x["rarity"]:x["weight"] for x in value["rewards"]}}
            for key,value in CRATE_CONFIG.items()
        },
        "coupon_rules": {"fragments_per_coupon":10,"coupon_types":["classic","premium","supply"]},
        "note":"Prototype economy tuning, not official PUBG pricing or drop rates."
    }

@app.get("/player/{player_id}/wallet")
def get_wallet(player_id: str):
    return {"player_id":player_id, **ensure_wallet(player_id)}

@app.post("/player/{player_id}/match/{match_id}/claim-bp")
def claim_match_bp(player_id: str, match_id: str, placement: int = Query(ge=1,le=100), kills: int = Query(default=0,ge=0,le=100)):
    # Prototype only: production must call this from a trusted match-server settlement service.
    bp = max(0, 100 + max(0, 101-placement)*8 + kills*20)
    with db() as con:
        try:
            con.execute("INSERT INTO match_rewards(match_id,player_id,bp_awarded) VALUES(?,?,?)",(match_id,player_id,bp))
        except sqlite3.IntegrityError:
            raise HTTPException(409,"Match reward already claimed")
        con.execute("INSERT OR IGNORE INTO wallets(player_id) VALUES(?)",(player_id,))
        con.execute("UPDATE wallets SET bp=bp+? WHERE player_id=?",(bp,player_id))
        con.execute("INSERT INTO economy_transactions(player_id,currency,amount,reason) VALUES(?,?,?,?)",(player_id,"BP",bp,"match_reward"))
    return {"status":"rewarded","bp_awarded":bp,"wallet":ensure_wallet(player_id)}

@app.post("/player/{player_id}/coupon/{coupon_type}/craft")
def craft_coupon(player_id: str, coupon_type: Literal["classic","premium","supply"], quantity: int = Query(default=1,ge=1,le=100)):
    key = coupon_type + "_fragments"
    needed = CRATE_CONFIG[coupon_type]["coupon_fragments"] * quantity
    with db() as con:
        con.execute("INSERT OR IGNORE INTO wallets(player_id) VALUES(?)",(player_id,))
        row = con.execute(f"SELECT {key} FROM wallets WHERE player_id=?",(player_id,)).fetchone()
        if row[0] < needed:
            raise HTTPException(400,"Not enough coupon fragments")
        con.execute(f"UPDATE wallets SET {key}={key}-? WHERE player_id=?",(needed,player_id))
    return {"status":"coupon_crafted","coupon_type":coupon_type,"coupons_added":quantity,"fragments_used":needed,"wallet":ensure_wallet(player_id)}

@app.post("/player/{player_id}/crate/{crate_id}/open")
def open_crate(player_id: str, crate_id: Literal["classic","premium","supply"], quantity: int = Query(default=1,ge=1,le=10), payment: Literal["currency","coupon"] = "currency"):
    config = CRATE_CONFIG[crate_id]
    with db() as con:
        con.execute("INSERT OR IGNORE INTO wallets(player_id) VALUES(?)",(player_id,))
        wallet = con.execute("SELECT bp,uc,classic_fragments,premium_fragments,supply_fragments FROM wallets WHERE player_id=?",(player_id,)).fetchone()
        if payment == "currency":
            col = config["currency"]
            cost = config["price"] * quantity
            balance = wallet[0] if col == "bp" else wallet[1]
            if balance < cost:
                raise HTTPException(400,f"Not enough {col.upper()}")
            con.execute(f"UPDATE wallets SET {col}={col}-? WHERE player_id=?",(cost,player_id))
            con.execute("INSERT INTO economy_transactions(player_id,currency,amount,reason) VALUES(?,?,?,?)",(player_id,col.upper(),-cost,crate_id+"_crate"))
        else:
            col = config["fragment_key"]
            # One complete coupon is represented by 10 fragments; consume that amount per opening.
            cost = config["coupon_fragments"] * quantity
            index = {"classic_fragments":2,"premium_fragments":3,"supply_fragments":4}[col]
            if wallet[index] < cost:
                raise HTTPException(400,"Not enough complete-coupon equivalent fragments")
            con.execute(f"UPDATE wallets SET {col}={col}-? WHERE player_id=?",(cost,player_id))
    won = []
    with db() as con:
        for _ in range(quantity):
            reward = weighted_crate_reward(crate_id)
            con.execute("INSERT INTO owned_cosmetics(player_id,item_id,crate_id,rarity) VALUES(?,?,?,?)",(player_id,reward["item_id"],crate_id,reward["rarity"]))
            won.append(reward)
    return {"status":"crate_opened","crate_id":crate_id,"payment":payment,"rewards":won,"wallet":ensure_wallet(player_id)}

@app.get("/player/{player_id}/collection")
def get_collection(player_id: str):
    with db() as con:
        rows = con.execute("SELECT item_id,crate_id,rarity,obtained_at FROM owned_cosmetics WHERE player_id=? ORDER BY id DESC",(player_id,)).fetchall()
    return {"player_id":player_id,"items":[{"item_id":r[0],"crate_id":r[1],"rarity":r[2],"obtained_at":r[3]} for r in rows]}

@app.get("/player/{player_id}/economy/history")
def economy_history(player_id: str, limit: int = Query(default=50,ge=1,le=200)):
    with db() as con:
        rows = con.execute("SELECT currency,amount,reason,created_at FROM economy_transactions WHERE player_id=? ORDER BY id DESC LIMIT ?",(player_id,limit)).fetchall()
    return {"player_id":player_id,"transactions":[{"currency":r[0],"amount":r[1],"reason":r[2],"created_at":r[3]} for r in rows]}



# v7 classic Erangel match clock, spawn-island countdown, blue-zone phases and result.
import time
import random
import math

ERANGEL_OLD_ZONE_PHASES = [
    {"wait":150,"move":300,"dps":0.6,"radius_ratio":0.40},
    {"wait":200,"move":140,"dps":0.8,"radius_ratio":0.55},
    {"wait":150,"move":90,"dps":1.0,"radius_ratio":0.60},
    {"wait":120,"move":60,"dps":3.0,"radius_ratio":0.60},
    {"wait":120,"move":40,"dps":5.0,"radius_ratio":0.65},
    {"wait":90,"move":30,"dps":8.0,"radius_ratio":0.65},
    {"wait":90,"move":30,"dps":10.0,"radius_ratio":0.65},
    {"wait":60,"move":30,"dps":14.0,"radius_ratio":0.70},
    {"wait":15,"move":15,"dps":18.0,"radius_ratio":0.001},
]
SPAWN_ISLAND_SECONDS = 40
ERANGEL_MAP_RADIUS_METERS = 4000

with db() as con:
    con.execute("CREATE TABLE IF NOT EXISTS match_sessions (match_id TEXT PRIMARY KEY, map_name TEXT NOT NULL, status TEXT NOT NULL, started_at REAL NOT NULL, state_json TEXT NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS match_results (match_id TEXT PRIMARY KEY, winner_id TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, result_json TEXT NOT NULL)")

def get_match_session(match_id: str):
    with db() as con:
        row = con.execute("SELECT state_json FROM match_sessions WHERE match_id=?", (match_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Match session not found")
    return json.loads(row[0])

def save_match_session(match_id: str, state: dict):
    with db() as con:
        con.execute("UPDATE match_sessions SET status=?, state_json=? WHERE match_id=?", (state["status"], json.dumps(state), match_id))

def _circle_for_phase(previous: dict, ratio: float, phase_number: int):
    old_radius = previous["radius_m"]
    radius = max(3.0, old_radius * ratio)
    max_offset = max(0.0, old_radius - radius)
    # Keep the next circle fully within the previous circle.
    angle = random.random() * math.tau
    distance = random.random() * max_offset
    return {
        "x": previous["x"] + math.cos(angle) * distance,
        "y": previous["y"] + math.sin(angle) * distance,
        "radius_m": radius,
        "phase": phase_number,
    }

def _zone_snapshot(state: dict, now: float):
    if state["status"] == "spawn_island":
        remaining = max(0, int(math.ceil(state["spawn_island_ends_at"] - now)))
        return {"status":"spawn_island","countdown_seconds":remaining,"phase":0,"message":"Waiting for players to board the plane"}
    if state["status"] in {"finished","cancelled"}:
        return {"status":state["status"],"winner_id":state.get("winner_id"),"phase":state.get("phase",0)}
    elapsed = max(0.0, now - state["match_started_at"])
    cursor = 0.0
    phase_data = None
    for index, phase in enumerate(ERANGEL_OLD_ZONE_PHASES, start=1):
        wait_end = cursor + phase["wait"]
        move_end = wait_end + phase["move"]
        if elapsed < wait_end:
            phase_data = {"phase":index,"state":"waiting","seconds_remaining":math.ceil(wait_end-elapsed),"wait_seconds":phase["wait"],"move_seconds":phase["move"],"dps":phase["dps"],"circle":state["circles"][index-1]}
            break
        if elapsed < move_end:
            phase_data = {"phase":index,"state":"shrinking","seconds_remaining":math.ceil(move_end-elapsed),"wait_seconds":phase["wait"],"move_seconds":phase["move"],"dps":phase["dps"],"circle":state["circles"][index-1]}
            break
        cursor = move_end
    if phase_data is None:
        phase_data = {"phase":len(ERANGEL_OLD_ZONE_PHASES),"state":"final","seconds_remaining":0,"wait_seconds":0,"move_seconds":0,"dps":ERANGEL_OLD_ZONE_PHASES[-1]["dps"],"circle":state["circles"][-1]}
    phase_data.update({"status":"in_match","elapsed_seconds":int(elapsed),"blue_zone_dps":phase_data["dps"],"next_circle":state["circles"][min(phase_data["phase"],len(state["circles"])-1)]})
    return phase_data

@app.get("/maps/erangel/classic-config")
def erangel_classic_config():
    return {
        "map":"Erangel","mode":"classic","spawn_island_countdown_seconds":SPAWN_ISLAND_SECONDS,
        "zone_timing_source":"published old Erangel wait/move timing reference; not verified PUBG Mobile 0.13.0 server files",
        "phases":[{"phase":i+1,**p} for i,p in enumerate(ERANGEL_OLD_ZONE_PHASES)],
        "total_zone_seconds":sum(p["wait"]+p["move"] for p in ERANGEL_OLD_ZONE_PHASES)
    }

@app.post("/match/{match_id}/start-erangel")
def start_erangel_match(match_id: str):
    if not match_id or len(match_id) > 64:
        raise HTTPException(400,"Invalid match_id")
    with db() as con:
        if con.execute("SELECT 1 FROM match_sessions WHERE match_id=?",(match_id,)).fetchone():
            raise HTTPException(409,"Match session already exists")
    now = time.time()
    first_circle = {"x":random.uniform(-500,500),"y":random.uniform(-500,500),"radius_m":ERANGEL_MAP_RADIUS_METERS,"phase":1}
    circles = [first_circle]
    for index, phase in enumerate(ERANGEL_OLD_ZONE_PHASES[1:], start=2):
        circles.append(_circle_for_phase(circles[-1],phase["radius_ratio"],index))
    state = {
        "match_id":match_id,"map":"Erangel","mode":"classic","status":"spawn_island",
        "created_at":now,"spawn_island_ends_at":now+SPAWN_ISLAND_SECONDS,
        "match_started_at":now+SPAWN_ISLAND_SECONDS,"circles":circles,"phase":0,
        "winner_id":None
    }
    with db() as con:
        con.execute("INSERT INTO match_sessions(match_id,map_name,status,started_at,state_json) VALUES(?,?,?,?,?)",(match_id,"Erangel","spawn_island",now,json.dumps(state)))
    return {"status":"match_created","match_id":match_id,"map":"Erangel","spawn_island_countdown_seconds":SPAWN_ISLAND_SECONDS,"zone_schedule":"classic_old_erangel_reference","first_circle":circles[0]}

@app.get("/match/{match_id}/battle-state")
def erangel_battle_state(match_id: str):
    state = get_match_session(match_id)
    now = time.time()
    if state["status"] == "spawn_island" and now >= state["spawn_island_ends_at"]:
        state["status"] = "in_match"
        save_match_session(match_id,state)
    snapshot = _zone_snapshot(state,now)
    snapshot["match_id"] = match_id
    snapshot["map"] = state["map"]
    snapshot["winner_id"] = state.get("winner_id")
    return snapshot

@app.post("/match/{match_id}/zone/damage")
def apply_zone_damage(match_id: str, player_id: str, seconds_outside: int = Query(default=1,ge=1,le=60)):
    state = get_match_session(match_id)
    snapshot = _zone_snapshot(state,time.time())
    if snapshot["status"] != "in_match":
        raise HTTPException(409,"Blue-zone damage is only applied during an active match")
    player = ensure_v4_state(get_player(player_id))
    damage = int(math.ceil(snapshot["blue_zone_dps"] * seconds_outside))
    player["health"] = max(0,player["health"]-damage)
    save_player(player_id,player)
    return {"player_id":player_id,"blue_zone_dps":snapshot["blue_zone_dps"],"seconds_outside":seconds_outside,"damage":damage,"health":player["health"],"eliminated":player["health"]<=0}

@app.post("/match/{match_id}/finish")
def finish_erangel_match(match_id: str, winner_id: str):
    state = get_match_session(match_id)
    if state["status"] == "finished":
        raise HTTPException(409,"Match already finished")
    with db() as con:
        roster = con.execute("SELECT player_id,state_json FROM players").fetchall()
    participants = []
    for pid, raw in roster:
        pstate = json.loads(raw)
        m = pstate.get("match",{})
        if m.get("status") == "in_match" and m.get("match_id") == match_id:
            participants.append(pid)
    if participants and winner_id not in participants:
        raise HTTPException(400,"Winner must belong to this match roster")
    state["status"] = "finished"
    state["winner_id"] = winner_id
    state["finished_at"] = time.time()
    save_match_session(match_id,state)
    result = {"match_id":match_id,"winner_id":winner_id,"banner":"WINNER WINNER CHICKEN DINNER","map":"Erangel"}
    with db() as con:
        con.execute("INSERT INTO match_results(match_id,winner_id,result_json) VALUES(?,?,?)",(match_id,winner_id,json.dumps(result)))
    return {"status":"match_finished","result":result}

@app.get("/match/{match_id}/result")
def get_erangel_result(match_id: str):
    with db() as con:
        row = con.execute("SELECT result_json FROM match_results WHERE match_id=?",(match_id,)).fetchone()
    if row is None:
        raise HTTPException(404,"Match result not available")
    return json.loads(row[0])



# v8 throwable handling: pin audio events, frag cooking, cancel and impact effects.
THROWABLE_PROFILES = {
    "frag_grenade": {"pin_sound":"frag_pin_pull","pin_audible_radius_m":20,"fuse_seconds":5.0,"cookable":True,"detonate_on_impact":False,"effect":"explosion"},
    "smoke_grenade": {"pin_sound":"smoke_pin_pull","pin_audible_radius_m":20,"fuse_seconds":1.0,"cookable":False,"detonate_on_impact":True,"effect":"smoke_cloud"},
    "molotov": {"pin_sound":"molotov_prepare","pin_audible_radius_m":20,"fuse_seconds":0.0,"cookable":False,"detonate_on_impact":True,"effect":"fire_area"},
}
THROWABLE_ACTIONS = {}

class ThrowableActionInput(BaseModel):
    item_id: Literal["frag_grenade","smoke_grenade","molotov"]
    action_id: str = Field(min_length=1,max_length=64)

@app.get("/throwables/config")
def throwables_config():
    return {
        "profiles": THROWABLE_PROFILES,
        "frag_cooking": {"total_fuse_seconds":5.0,"cook_starts_on_pin_pull":True,"cancel_after_cooking_starts":False},
        "audio": {"pin_pull_event":"play_for_nearby_clients","audible_radius_m":20},
        "cancel": {"allowed_before_pin_pull":True,"allowed_after_pin_pull_before_fuse":True,"allowed_after_cooking_starts":False},
        "note":"Prototype tuning. UE4 client must render throw animations, pin audio, fuse UI, projectile physics, smoke and fire effects."
    }

@app.post("/player/{player_id}/throwable/begin")
def begin_throwable_action(player_id: str, data: ThrowableActionInput):
    state = ensure_v4_state(get_player(player_id))
    if data.action_id in THROWABLE_ACTIONS:
        raise HTTPException(409,"Throwable action already exists")
    if state["throwables"].get(data.item_id,0) < 1:
        raise HTTPException(400,"Throwable not available")
    profile = THROWABLE_PROFILES[data.item_id]
    now = time.time()
    action = {
        "action_id":data.action_id,"player_id":player_id,"item_id":data.item_id,
        "status":"aiming","started_at":now,"pin_pulled_at":None,
        "fuse_ends_at":None,"cookable":profile["cookable"]
    }
    THROWABLE_ACTIONS[data.action_id] = action
    return {"status":"throwable_selected","action":action,"audio_event":None}

@app.post("/player/{player_id}/throwable/{action_id}/pull-pin")
def pull_throwable_pin(player_id: str, action_id: str):
    action = THROWABLE_ACTIONS.get(action_id)
    if action is None or action["player_id"] != player_id:
        raise HTTPException(404,"Throwable action not found")
    if action["status"] != "aiming":
        raise HTTPException(409,"Pin cannot be pulled in the current state")
    profile = THROWABLE_PROFILES[action["item_id"]]
    now = time.time()
    action["pin_pulled_at"] = now
    action["status"] = "cooking" if profile["cookable"] else "ready_to_throw"
    action["fuse_ends_at"] = now + profile["fuse_seconds"] if profile["cookable"] else None
    return {
        "status":action["status"],"action":action,
        "audio_event":{"name":profile["pin_sound"],"audible_radius_m":profile["pin_audible_radius_m"]},
        "fuse_seconds":profile["fuse_seconds"] if profile["cookable"] else None
    }

@app.post("/player/{player_id}/throwable/{action_id}/cancel")
def cancel_throwable_action(player_id: str, action_id: str):
    action = THROWABLE_ACTIONS.get(action_id)
    if action is None or action["player_id"] != player_id:
        raise HTTPException(404,"Throwable action not found")
    if action["status"] == "cooking":
        raise HTTPException(409,"Cannot cancel after frag fuse has started; throw it")
    if action["status"] not in {"aiming","ready_to_throw"}:
        raise HTTPException(409,"Throwable action cannot be cancelled")
    action["status"] = "cancelled"
    del THROWABLE_ACTIONS[action_id]
    return {"status":"cancelled","item_id":action["item_id"],"inventory_consumed":False}

@app.post("/player/{player_id}/throwable/{action_id}/throw")
def release_throwable_action(player_id: str, action_id: str):
    action = THROWABLE_ACTIONS.get(action_id)
    if action is None or action["player_id"] != player_id:
        raise HTTPException(404,"Throwable action not found")
    if action["status"] not in {"cooking","ready_to_throw"}:
        raise HTTPException(409,"Pull the pin or prepare the throwable first")
    now = time.time()
    if action["fuse_ends_at"] is not None and now >= action["fuse_ends_at"]:
        action["status"] = "detonated_in_hand"
        raise HTTPException(409,"Frag fuse expired before release")
    state = ensure_v4_state(get_player(player_id))
    item_id = action["item_id"]
    if state["throwables"].get(item_id,0) < 1:
        raise HTTPException(400,"Throwable inventory changed")
    state["throwables"][item_id] -= 1
    save_player(player_id,state)
    profile = THROWABLE_PROFILES[item_id]
    elapsed = round(now-action["pin_pulled_at"],2) if action["pin_pulled_at"] else 0
    result = {
        "status":"thrown","item_id":item_id,"cook_elapsed_seconds":elapsed,
        "remaining_fuse_seconds":max(0,round(profile["fuse_seconds"]-elapsed,2)) if profile["cookable"] else None,
        "impact_effect":profile["effect"],"detonate_on_impact":profile["detonate_on_impact"],
        "inventory_remaining":state["throwables"][item_id]
    }
    del THROWABLE_ACTIONS[action_id]
    return result

@app.get("/player/{player_id}/throwable/{action_id}")
def throwable_action_state(player_id: str, action_id: str):
    action = THROWABLE_ACTIONS.get(action_id)
    if action is None or action["player_id"] != player_id:
        raise HTTPException(404,"Throwable action not found")
    remaining = None
    if action["fuse_ends_at"] is not None:
        remaining = max(0,round(action["fuse_ends_at"]-time.time(),2))
    return {"action":action,"fuse_remaining_seconds":remaining}
