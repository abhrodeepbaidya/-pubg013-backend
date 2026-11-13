"""Additional gameplay API: combat validation, world loot, vehicles, TDM, state and sessions.

This is a SQLite-backed prototype API. A trusted dedicated match process must own
these mutations in production; clients must never be trusted to report hits/damage.
"""
from __future__ import annotations

import json
import math
import os
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(tags=["gameplay-systems"])
DB_PATH = Path(__file__).resolve().parent / "game.db"
MAP_LIMIT = 4000.0
WEAPON_RULES = {
    "M416": {"damage": 41, "rpm": 700, "mag": 30, "range": 500},
    "AKM": {"damage": 47, "rpm": 600, "mag": 30, "range": 450},
    "SCAR-L": {"damage": 41, "rpm": 600, "mag": 30, "range": 500},
    "M16A4": {"damage": 43, "rpm": 800, "mag": 30, "range": 550},
    "UMP9": {"damage": 39, "rpm": 600, "mag": 30, "range": 300},
    "UMP45": {"damage": 39, "rpm": 600, "mag": 25, "range": 300},
    "UZI": {"damage": 26, "rpm": 1200, "mag": 25, "range": 150},
    "Vector": {"damage": 31, "rpm": 1200, "mag": 19, "range": 200},
    "Kar98k": {"damage": 75, "rpm": 40, "mag": 5, "range": 800},
    "M24": {"damage": 79, "rpm": 40, "mag": 5, "range": 850},
    "AWM": {"damage": 105, "rpm": 40, "mag": 5, "range": 1000},
    "Mini14": {"damage": 46, "rpm": 600, "mag": 20, "range": 700},
    "SKS": {"damage": 53, "rpm": 600, "mag": 10, "range": 650},
    "DP-28": {"damage": 51, "rpm": 550, "mag": 47, "range": 500},
    "M249": {"damage": 45, "rpm": 800, "mag": 100, "range": 550},
    "P92": {"damage": 35, "rpm": 400, "mag": 15, "range": 100},
    "P1911": {"damage": 41, "rpm": 300, "mag": 7, "range": 100},
}

def connect():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    return con

def init():
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS combat_state(
          match_id TEXT NOT NULL, player_id TEXT NOT NULL, health REAL NOT NULL DEFAULT 100,
          alive INTEGER NOT NULL DEFAULT 1, last_shot REAL NOT NULL DEFAULT 0,
          PRIMARY KEY(match_id,player_id));
        CREATE TABLE IF NOT EXISTS world_loot(
          loot_id TEXT PRIMARY KEY, match_id TEXT NOT NULL, item_id TEXT NOT NULL,
          quantity INTEGER NOT NULL, x REAL NOT NULL, y REAL NOT NULL, z REAL NOT NULL,
          state TEXT NOT NULL DEFAULT 'ground', owner_id TEXT, updated_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_world_loot_match ON world_loot(match_id,state);
        CREATE TABLE IF NOT EXISTS world_vehicles(
          vehicle_id TEXT PRIMARY KEY, match_id TEXT NOT NULL, vehicle_type TEXT NOT NULL,
          x REAL NOT NULL, y REAL NOT NULL, z REAL NOT NULL, health REAL NOT NULL DEFAULT 1000,
          fuel REAL NOT NULL DEFAULT 100, driver_id TEXT, updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS death_crates(
          crate_id TEXT PRIMARY KEY, match_id TEXT NOT NULL, owner_id TEXT NOT NULL,
          x REAL NOT NULL, y REAL NOT NULL, z REAL NOT NULL, contents_json TEXT NOT NULL,
          created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS player_sessions(
          player_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, expires_at REAL NOT NULL,
          created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS tdm_state(
          match_id TEXT PRIMARY KEY, score_red INTEGER NOT NULL DEFAULT 0,
          score_blue INTEGER NOT NULL DEFAULT 0, target_score INTEGER NOT NULL DEFAULT 40,
          time_limit_seconds INTEGER NOT NULL DEFAULT 600, started_at REAL NOT NULL,
          status TEXT NOT NULL DEFAULT 'active');
        CREATE TABLE IF NOT EXISTS tdm_players(
          match_id TEXT NOT NULL, player_id TEXT NOT NULL, team TEXT NOT NULL,
          kills INTEGER NOT NULL DEFAULT 0, deaths INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(match_id,player_id));
        CREATE TABLE IF NOT EXISTS player_inventory_events(
          event_id TEXT PRIMARY KEY, match_id TEXT NOT NULL, player_id TEXT NOT NULL,
          event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at REAL NOT NULL);
        """)

@router.get("/maps/erangel/manifest")
def erangel_manifest():
    return {
      "map_id":"erangel_classic", "display_name":"Erangel",
      "coordinate_bounds":{"min_x":-MAP_LIMIT,"max_x":MAP_LIMIT,"min_y":-MAP_LIMIT,"max_y":MAP_LIMIT},
      "poi":[
        {"id":"georgopol","x":-2500,"y":1800},{"id":"hospital","x":-1900,"y":1300},
        {"id":"rozok","x":-300,"y":300},{"id":"school","x":200,"y":500},
        {"id":"pochinki","x":0,"y":-1100},{"id":"military_base","x":2500,"y":-2500},
        {"id":"novorepnoye","x":3300,"y":-2200},{"id":"yasnaya","x":1200,"y":2500}
      ],
      "asset_status":"UE4 map package and collision/navmesh must be integrated by the game client; this manifest is not a playable map asset."
    }

@router.post("/matches/{match_id}/world/generate-loot")
def generate_world_loot(match_id: str, seed: int = Query(default=1300, ge=0, le=2147483647), count: int = Query(default=250, ge=1, le=2000)):
    import random
    rng = random.Random(seed)
    table = [("5.56mm",30),("7.62mm",30),("9mm",30),("bandage",5),("first_aid_kit",1),
             ("energy_drink",1),("M416",1),("AKM",1),("Kar98k",1),("red_dot",1),
             ("scope_4x",1),("frag_grenade",1),("smoke_grenade",1)]
    created=[]
    with connect() as con:
        for _ in range(count):
            item,qty=rng.choice(table)
            loot_id="loot-"+uuid.uuid4().hex[:16]
            x,y=rng.uniform(-MAP_LIMIT,MAP_LIMIT),rng.uniform(-MAP_LIMIT,MAP_LIMIT)
            con.execute("INSERT INTO world_loot VALUES(?,?,?,?,?,?,?,'ground',NULL,?)",
                        (loot_id,match_id,item,qty,x,y,0.0,time.time()))
            created.append({"loot_id":loot_id,"item_id":item,"quantity":qty,"x":round(x,2),"y":round(y,2),"z":0})
    return {"match_id":match_id,"created":len(created),"loot":created,
            "note":"Prototype procedural loot placement; production needs POI/building spawn tables and UE4 asset coordinates."}

@router.get("/matches/{match_id}/world/loot")
def list_world_loot(match_id: str, x: float | None = None, y: float | None = None, radius: float = Query(default=100,gt=0,le=1000)):
    with connect() as con:
        if x is None or y is None:
            rows=con.execute("SELECT * FROM world_loot WHERE match_id=? AND state='ground' LIMIT 500",(match_id,)).fetchall()
        else:
            rows=con.execute("SELECT * FROM world_loot WHERE match_id=? AND state='ground' AND ((x-?)*(x-?)+(y-?)*(y-?))<=? LIMIT 500",(match_id,x,x,y,y,radius*radius)).fetchall()
    return {"match_id":match_id,"loot":[dict(r) for r in rows]}

class LootAction(BaseModel):
    player_id: str = Field(min_length=1,max_length=64)
    loot_id: str = Field(min_length=1,max_length=64)
    quantity: int = Field(default=1,ge=1,le=1000)
    x: float
    y: float

@router.post("/matches/{match_id}/inventory/pickup")
def pickup_loot(match_id: str, data: LootAction):
    if not all(math.isfinite(v) for v in (data.x,data.y)):
        raise HTTPException(400,"Invalid player position")
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row=con.execute("SELECT * FROM world_loot WHERE loot_id=? AND match_id=? AND state='ground'",(data.loot_id,match_id)).fetchone()
        if row is None: raise HTTPException(404,"Loot unavailable or already collected")
        if math.dist((data.x,data.y),(row["x"],row["y"]))>5:
            raise HTTPException(400,"Player is too far from loot")
        if data.quantity>row["quantity"]: raise HTTPException(400,"Requested quantity exceeds ground stack")
        remaining=row["quantity"]-data.quantity
        if remaining:
            con.execute("UPDATE world_loot SET quantity=? WHERE loot_id=?",(remaining,data.loot_id))
        else:
            con.execute("UPDATE world_loot SET state='picked_up',owner_id=?,updated_at=? WHERE loot_id=?",(data.player_id,time.time(),data.loot_id))
        con.execute("INSERT INTO player_inventory_events VALUES(?,?,?,?,?,?)",
                    (uuid.uuid4().hex,match_id,data.player_id,"pickup",json.dumps({"loot_id":data.loot_id,"item_id":row["item_id"],"quantity":data.quantity}),time.time()))
    return {"status":"picked_up","item_id":row["item_id"],"quantity":data.quantity}

class DropLoot(BaseModel):
    player_id: str = Field(min_length=1,max_length=64)
    item_id: str = Field(min_length=1,max_length=64)
    quantity: int = Field(ge=1,le=1000)
    x: float
    y: float
    z: float = 0

@router.post("/matches/{match_id}/inventory/drop")
def drop_loot(match_id: str, data: DropLoot):
    if not all(math.isfinite(v) for v in (data.x,data.y,data.z)) or abs(data.x)>MAP_LIMIT or abs(data.y)>MAP_LIMIT:
        raise HTTPException(400,"Invalid drop position")
    loot_id="loot-"+uuid.uuid4().hex[:16]
    with connect() as con:
        con.execute("INSERT INTO world_loot VALUES(?,?,?,?,?,?,?,'ground',NULL,?)",
                    (loot_id,match_id,data.item_id,data.quantity,data.x,data.y,data.z,time.time()))
        con.execute("INSERT INTO player_inventory_events VALUES(?,?,?,?,?,?)",
                    (uuid.uuid4().hex,match_id,data.player_id,"drop",data.model_dump_json(),time.time()))
    return {"status":"dropped","loot_id":loot_id}

@router.post("/matches/{match_id}/death-crates")
def create_death_crate(match_id: str, owner_id: str, x: float, y: float, z: float = 0, contents: dict = {}):
    if not all(math.isfinite(v) for v in (x,y,z)) or abs(x)>MAP_LIMIT or abs(y)>MAP_LIMIT:
        raise HTTPException(400,"Invalid crate position")
    crate_id="crate-"+uuid.uuid4().hex[:16]
    with connect() as con:
        con.execute("INSERT INTO death_crates VALUES(?,?,?,?,?,?,?,?)",
                    (crate_id,match_id,owner_id,x,y,z,json.dumps(contents),time.time()))
    return {"status":"created","crate_id":crate_id,"position":{"x":x,"y":y,"z":z}}

@router.get("/matches/{match_id}/death-crates/{crate_id}")
def get_death_crate(match_id: str, crate_id: str):
    with connect() as con:
        row=con.execute("SELECT * FROM death_crates WHERE match_id=? AND crate_id=?",(match_id,crate_id)).fetchone()
    if row is None: raise HTTPException(404,"Death crate not found")
    return {**dict(row),"contents":json.loads(row["contents_json"])}

class FireInput(BaseModel):
    shooter_id: str
    target_id: str
    weapon_id: str
    hit_zone: Literal["head","chest","stomach","arms","legs"]
    distance_m: float = Field(ge=0,le=2000)
    client_shot_id: str = Field(min_length=1,max_length=64)
    weapon_slot: Literal["primary_1","primary_2","pistol"] = "primary_1"

@router.post("/matches/{match_id}/combat/fire")
def validate_fire(match_id: str, data: FireInput):
    """Prototype fire validator; production hit positions must be server-rewound and ray-tested."""
    import importlib
    if data.weapon_id not in WEAPON_RULES: raise HTTPException(400,"Weapon has no configured server profile")
    rules=WEAPON_RULES[data.weapon_id]
    now=time.time()
    # Consume ammunition from the persisted equipped weapon before applying damage.
    with connect() as con:
        player_row=con.execute("SELECT state_json FROM players WHERE player_id=?",(data.shooter_id,)).fetchone()
        if player_row is None:
            raise HTTPException(404,"Shooter player profile not found")
        player_state=json.loads(player_row["state_json"])
        equipped=player_state.get("weapons",{}).get(data.weapon_slot)
        if not equipped or equipped.get("weapon_id") != data.weapon_id:
            raise HTTPException(400,"Weapon is not equipped in the requested slot")
        if int(equipped.get("ammo",0)) <= 0:
            raise HTTPException(409,"Magazine empty")
        equipped["ammo"]=int(equipped["ammo"])-1
        con.execute("UPDATE players SET state_json=? WHERE player_id=?",(json.dumps(player_state),data.shooter_id))
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        shooter=con.execute("SELECT * FROM combat_state WHERE match_id=? AND player_id=?",(match_id,data.shooter_id)).fetchone()
        target=con.execute("SELECT * FROM combat_state WHERE match_id=? AND player_id=?",(match_id,data.target_id)).fetchone()
        if shooter is None:
            con.execute("INSERT INTO combat_state(match_id,player_id) VALUES(?,?)",(match_id,data.shooter_id))
            shooter=con.execute("SELECT * FROM combat_state WHERE match_id=? AND player_id=?",(match_id,data.shooter_id)).fetchone()
        if target is None:
            con.execute("INSERT INTO combat_state(match_id,player_id) VALUES(?,?)",(match_id,data.target_id))
            target=con.execute("SELECT * FROM combat_state WHERE match_id=? AND player_id=?",(match_id,data.target_id)).fetchone()
        if not shooter["alive"] or not target["alive"]: raise HTTPException(409,"Shooter or target is eliminated")
        interval=60.0/rules["rpm"]
        if now-shooter["last_shot"] < interval*0.85: raise HTTPException(429,"Fire rate exceeds weapon limit")
        con.execute("UPDATE combat_state SET last_shot=? WHERE match_id=? AND player_id=?",(now,match_id,data.shooter_id))
        multiplier={"head":2.0,"chest":1.0,"stomach":1.0,"arms":0.75,"legs":0.65}[data.hit_zone]
        range_factor=max(0.35,1.0-data.distance_m/max(1,rules["range"])*0.5)
        damage=round(rules["damage"]*multiplier*range_factor,1)
        health=max(0.0,target["health"]-damage)
        con.execute("UPDATE combat_state SET health=?,alive=? WHERE match_id=? AND player_id=?",
                    (health,int(health>0),match_id,data.target_id))
    return {"status":"validated_prototype","shot_id":data.client_shot_id,"damage":damage,"target_health":health,
            "eliminated":health<=0,"ammo_validation":"equipped slot checked and one round consumed",
            "hit_validation":"client-supplied hit zone/distance are not authoritative; integrate server rewind and raycast"}

class VehicleSpawn(BaseModel):
    vehicle_type: Literal["jeep","dacia","buggy","motorcycle","boat"]
    x: float
    y: float
    z: float = 0

@router.post("/matches/{match_id}/vehicles/spawn")
def spawn_vehicle(match_id: str, data: VehicleSpawn):
    if not all(math.isfinite(v) for v in (data.x,data.y,data.z)) or abs(data.x)>MAP_LIMIT or abs(data.y)>MAP_LIMIT:
        raise HTTPException(400,"Invalid vehicle spawn coordinates")
    vid="veh-"+uuid.uuid4().hex[:12]
    with connect() as con:
        con.execute("INSERT INTO world_vehicles VALUES(?,?,?,?,?,?,1000,100,NULL,?)",(vid,match_id,data.vehicle_type,data.x,data.y,data.z,time.time()))
    return {"vehicle_id":vid,"vehicle_type":data.vehicle_type,"health":1000,"fuel":100}

class VehicleControl(BaseModel):
    player_id: str
    x: float
    y: float
    z: float = 0
    fuel_used: float = Field(default=0,ge=0,le=5)

@router.post("/matches/{match_id}/vehicles/{vehicle_id}/drive")
def drive_vehicle(match_id: str, vehicle_id: str, data: VehicleControl):
    if not all(math.isfinite(v) for v in (data.x,data.y,data.z)) or abs(data.x)>MAP_LIMIT or abs(data.y)>MAP_LIMIT:
        raise HTTPException(400,"Invalid vehicle position")
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row=con.execute("SELECT * FROM world_vehicles WHERE match_id=? AND vehicle_id=?",(match_id,vehicle_id)).fetchone()
        if row is None: raise HTTPException(404,"Vehicle not found")
        if row["driver_id"] not in (None,data.player_id): raise HTTPException(409,"Vehicle already occupied")
        fuel=max(0,row["fuel"]-data.fuel_used)
        con.execute("UPDATE world_vehicles SET x=?,y=?,z=?,fuel=?,driver_id=?,updated_at=? WHERE vehicle_id=?",
                    (data.x,data.y,data.z,fuel,data.player_id,time.time(),vehicle_id))
    return {"vehicle_id":vehicle_id,"position":{"x":data.x,"y":data.y,"z":data.z},"fuel":fuel}

@router.post("/matches/{match_id}/vehicles/{vehicle_id}/exit")
def exit_vehicle(match_id: str, vehicle_id: str, player_id: str):
    with connect() as con:
        cur=con.execute("UPDATE world_vehicles SET driver_id=NULL,updated_at=? WHERE match_id=? AND vehicle_id=? AND driver_id=?",
                        (time.time(),match_id,vehicle_id,player_id))
        if cur.rowcount==0: raise HTTPException(409,"Player is not driving this vehicle")
    return {"status":"exited"}

@router.post("/matches/{match_id}/vehicles/{vehicle_id}/damage")
def damage_vehicle(match_id: str, vehicle_id: str, damage: float = Query(gt=0,le=1000)):
    with connect() as con:
        row=con.execute("SELECT health FROM world_vehicles WHERE match_id=? AND vehicle_id=?",(match_id,vehicle_id)).fetchone()
        if row is None: raise HTTPException(404,"Vehicle not found")
        health=max(0,row["health"]-damage)
        con.execute("UPDATE world_vehicles SET health=?,updated_at=? WHERE vehicle_id=?",(health,time.time(),vehicle_id))
    return {"vehicle_id":vehicle_id,"health":health,"destroyed":health<=0}

@router.post("/matches/{match_id}/tdm/start")
def start_tdm(match_id: str, target_score: int = Query(default=40,ge=1,le=200), time_limit_seconds: int = Query(default=600,ge=60,le=3600)):
    with connect() as con:
        if con.execute("SELECT 1 FROM tdm_state WHERE match_id=?",(match_id,)).fetchone(): raise HTTPException(409,"TDM already started")
        con.execute("INSERT INTO tdm_state(match_id,target_score,time_limit_seconds,started_at) VALUES(?,?,?,?)",
                    (match_id,target_score,time_limit_seconds,time.time()))
    return {"match_id":match_id,"map":"tdm_factory_depot","teams":["red","blue"],"target_score":target_score,
            "time_limit_seconds":time_limit_seconds,
            "spawn_points":{"red":[[-120,0,0],[-100,80,0]],"blue":[[120,0,0],[100,-80,0]]},
            "asset_status":"UE4 Factory map, collision, navmesh and exact spawn transforms must be supplied by the client project."}

@router.post("/matches/{match_id}/tdm/assign")
def assign_tdm_player(match_id: str, player_id: str, team: Literal["red","blue"]):
    with connect() as con:
        if con.execute("SELECT 1 FROM tdm_state WHERE match_id=?",(match_id,)).fetchone() is None: raise HTTPException(404,"TDM not started")
        con.execute("INSERT OR REPLACE INTO tdm_players(match_id,player_id,team) VALUES(?,?,?)",(match_id,player_id,team))
    return {"player_id":player_id,"team":team}

@router.post("/matches/{match_id}/tdm/kill")
def tdm_kill(match_id: str, killer_id: str, victim_id: str):
    with connect() as con:
        state=con.execute("SELECT * FROM tdm_state WHERE match_id=?",(match_id,)).fetchone()
        killer=con.execute("SELECT team FROM tdm_players WHERE match_id=? AND player_id=?",(match_id,killer_id)).fetchone()
        victim=con.execute("SELECT team FROM tdm_players WHERE match_id=? AND player_id=?",(match_id,victim_id)).fetchone()
        if state is None or killer is None or victim is None: raise HTTPException(404,"TDM match/player not found")
        if killer["team"]==victim["team"]: raise HTTPException(400,"Friendly fire does not score in this TDM ruleset")
        score_col="score_red" if killer["team"]=="red" else "score_blue"
        con.execute(f"UPDATE tdm_state SET {score_col}={score_col}+1 WHERE match_id=?",(match_id,))
        con.execute("UPDATE tdm_players SET kills=kills+1 WHERE match_id=? AND player_id=?",(match_id,killer_id))
        con.execute("UPDATE tdm_players SET deaths=deaths+1 WHERE match_id=? AND player_id=?",(match_id,victim_id))
        latest=con.execute("SELECT * FROM tdm_state WHERE match_id=?",(match_id,)).fetchone()
    winner="red" if latest["score_red"]>=latest["target_score"] else ("blue" if latest["score_blue"]>=latest["target_score"] else None)
    return {"score_red":latest["score_red"],"score_blue":latest["score_blue"],"winner":winner}

@router.get("/matches/{match_id}/tdm/state")
def tdm_state(match_id: str):
    with connect() as con:
        state=con.execute("SELECT * FROM tdm_state WHERE match_id=?",(match_id,)).fetchone()
        players=con.execute("SELECT * FROM tdm_players WHERE match_id=?",(match_id,)).fetchall()
    if state is None: raise HTTPException(404,"TDM not found")
    return {"match":dict(state),"players":[dict(p) for p in players]}

@router.get("/matches/{match_id}/replication/snapshot")
def replication_snapshot(match_id: str):
    from multiplayer import _db
    with _db() as con:
        rows=con.execute("SELECT player_id,x,y,z,yaw,health,alive,updated_at FROM multiplayer_player_states WHERE match_id=?",(match_id,)).fetchall()
    return {"match_id":match_id,"server_time":time.time(),"players":[dict(r) for r in rows],
            "transport":"existing websocket relay; snapshots are prototype state, not production authoritative replication"}

@router.get("/security/config")
def security_config():
    return {"session_tokens":"endpoint foundation below; issue tokens only after account authentication",
            "websocket_auth":"current websocket query player_id is not secure; replace with signed short-lived token validation",
            "anti_cheat":["server-side movement envelope checks","server-side fire cadence checks","inventory mutation ownership","rate limits","audit events"],
            "production_required":["TLS","signed JWT/session tokens","secret rotation","server rewind/raycast","dedicated match workers","cross-worker pubsub","PostgreSQL","immutable audit trail"]}

class SessionIssue(BaseModel):
    player_id: str = Field(min_length=1,max_length=64)
    ttl_seconds: int = Field(default=3600,ge=60,le=86400)

@router.post("/security/session/issue")
def issue_session(data: SessionIssue):
    token=secrets.token_urlsafe(32)
    import hashlib
    digest=hashlib.sha256(token.encode()).hexdigest()
    now=time.time()
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO player_sessions VALUES(?,?,?,?)",
                    (data.player_id,digest,now+data.ttl_seconds,now))
    return {"player_id":data.player_id,"access_token":token,"token_type":"Bearer","expires_in":data.ttl_seconds,
            "warning":"Prototype token issuer only; must be called after real account credential verification."}

@router.get("/security/session/validate")
def validate_session(token: str):
    import hashlib
    digest=hashlib.sha256(token.encode()).hexdigest()
    with connect() as con:
        row=con.execute("SELECT player_id,expires_at FROM player_sessions WHERE token_hash=?",(digest,)).fetchone()
    if row is None or row["expires_at"]<time.time(): raise HTTPException(401,"Invalid or expired session")
    return {"valid":True,"player_id":row["player_id"],"expires_at":row["expires_at"]}

@router.get("/production/readiness")
def production_readiness():
    return {"database":{"current":"SQLite prototype","target":"PostgreSQL via DATABASE_URL","status":"migration_required"},
            "game_servers":{"current":"single-process FastAPI/WebSocket","target":"dedicated per-match workers with Redis/NATS pubsub","status":"not_deployed"},
            "security":{"current":"prototype checks and token primitives","status":"not_production_ready"},
            "required_environment":["DATABASE_URL","SESSION_SIGNING_SECRET","REDIS_URL"],
            "note":"No PostgreSQL database, dedicated workers, Redis, or production secrets are provisioned by this code commit."}
