"""Matchmaking with 75 human slots + 25 AI bot slots for Classic.

Bots are roster entries with randomized plane-jump destinations. This module
does not yet implement autonomous bot navigation, looting, aiming or combat AI.
"""
from __future__ import annotations

import math
import random
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(tags=["matchmaking"])
DB_PATH = Path(__file__).resolve().parent / "game.db"
QUEUE_TTL_SECONDS = 180
CLASSIC_TARGET_PLAYERS = 100
CLASSIC_HUMAN_PLAYERS = 75
CLASSIC_BOT_PLAYERS = 25
TDM_TARGET_PLAYERS = 8
MAP_HALF_EXTENT = 4000.0
PLANE_ALTITUDE = 1500.0


def _connect():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    return con


def _init_matchmaking_db():
    with _connect() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS matchmaking_queue (
                player_id TEXT PRIMARY KEY, mode TEXT NOT NULL,
                perspective TEXT NOT NULL, region TEXT NOT NULL,
                enqueued_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                match_id TEXT
            )
        """)
        con.execute("""CREATE INDEX IF NOT EXISTS idx_matchmaking_bucket
            ON matchmaking_queue(mode,perspective,region,status,enqueued_at)""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS live_matches (
                match_id TEXT PRIMARY KEY, mode TEXT NOT NULL,
                perspective TEXT NOT NULL, region TEXT NOT NULL,
                status TEXT NOT NULL, target_players INTEGER NOT NULL,
                created_at REAL NOT NULL, started_at REAL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS live_match_players (
                match_id TEXT NOT NULL, player_id TEXT NOT NULL UNIQUE,
                team_id INTEGER, joined_at REAL NOT NULL,
                is_bot INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(match_id,player_id)
            )
        """)
        columns = {row["name"] for row in con.execute("PRAGMA table_info(live_match_players)")}
        if "is_bot" not in columns:
            con.execute("ALTER TABLE live_match_players ADD COLUMN is_bot INTEGER NOT NULL DEFAULT 0")
        con.execute("""
            CREATE TABLE IF NOT EXISTS match_deployments (
                match_id TEXT PRIMARY KEY, plane_start_json TEXT NOT NULL,
                plane_end_json TEXT NOT NULL, bot_jumps_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)


class QueueJoinInput(BaseModel):
    player_id: str = Field(min_length=1, max_length=64)
    mode: Literal["classic", "tdm"] = "classic"
    perspective: Literal["tpp", "fpp"] = "tpp"
    region: str = Field(default="asia", min_length=2, max_length=16)


def _target(mode: str) -> int:
    return CLASSIC_HUMAN_PLAYERS if mode == "classic" else TDM_TARGET_PLAYERS


def _clean_expired(con: sqlite3.Connection, now: float):
    con.execute("DELETE FROM matchmaking_queue WHERE status='queued' AND enqueued_at < ?",
                (now - QUEUE_TTL_SECONDS,))


def _queue_response(con: sqlite3.Connection, player_id: str):
    row = con.execute(
        "SELECT player_id,mode,perspective,region,enqueued_at,status,match_id "
        "FROM matchmaking_queue WHERE player_id=?", (player_id,)
    ).fetchone()
    if row is None:
        return None
    count = con.execute(
        "SELECT COUNT(*) FROM matchmaking_queue WHERE mode=? AND perspective=? "
        "AND region=? AND status='queued'",
        (row["mode"], row["perspective"], row["region"]),
    ).fetchone()[0]
    needed = _target(row["mode"])
    return {"player_id": row["player_id"], "mode": row["mode"],
            "perspective": row["perspective"], "region": row["region"],
            "status": row["status"], "match_id": row["match_id"],
            "queued_seconds": max(0, int(time.time()-row["enqueued_at"])),
            "players_in_queue": count, "real_players_required": needed,
            "bots_added_on_match": CLASSIC_BOT_PLAYERS if row["mode"] == "classic" else 0,
            "total_match_size": CLASSIC_TARGET_PLAYERS if row["mode"] == "classic" else TDM_TARGET_PLAYERS}


def _edge_point(edge: str) -> dict:
    if edge == "north":
        return {"x": random.uniform(-3500,3500), "y": MAP_HALF_EXTENT, "z": PLANE_ALTITUDE}
    if edge == "south":
        return {"x": random.uniform(-3500,3500), "y": -MAP_HALF_EXTENT, "z": PLANE_ALTITUDE}
    if edge == "east":
        return {"x": MAP_HALF_EXTENT, "y": random.uniform(-3500,3500), "z": PLANE_ALTITUDE}
    return {"x": -MAP_HALF_EXTENT, "y": random.uniform(-3500,3500), "z": PLANE_ALTITUDE}


def _make_deployment(match_id: str, bot_ids: list[str], now: float):
    edges = ["north", "south", "east", "west"]
    edge_a = random.choice(edges)
    edge_b = random.choice([edge for edge in edges if edge != edge_a])
    start, end = _edge_point(edge_a), _edge_point(edge_b)
    bot_jumps = []
    for bot_id in bot_ids:
        t = random.uniform(0.08, 0.92)
        x = start["x"] + (end["x"] - start["x"]) * t + random.uniform(-500, 500)
        y = start["y"] + (end["y"] - start["y"]) * t + random.uniform(-500, 500)
        x = max(-MAP_HALF_EXTENT, min(MAP_HALF_EXTENT, x))
        y = max(-MAP_HALF_EXTENT, min(MAP_HALF_EXTENT, y))
        bot_jumps.append({
            "player_id": bot_id,
            "jump_position": {"x": round(x, 2), "y": round(y, 2), "z": 0.0},
            "jump_delay_seconds": round(random.uniform(3, 45), 2),
            "landing_spread_m": round(random.uniform(15, 90), 2),
            "status": "scheduled"
        })
    with _connect() as con:
        con.execute(
            "INSERT INTO match_deployments(match_id,plane_start_json,plane_end_json,bot_jumps_json,created_at) "
            "VALUES(?,?,?,?,?)",
            (match_id, __import__("json").dumps(start), __import__("json").dumps(end),
             __import__("json").dumps(bot_jumps), now)
        )


@router.post("/matchmaking/join")
def join_queue(data: QueueJoinInput):
    _init_matchmaking_db()
    now = time.time()
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        _clean_expired(con, now)
        if con.execute("SELECT 1 FROM players WHERE player_id=?", (data.player_id,)).fetchone() is None:
            raise HTTPException(404, "Player not found; register or create the player first")
        current = con.execute(
            "SELECT status,mode,perspective,region FROM matchmaking_queue WHERE player_id=?",
            (data.player_id,)
        ).fetchone()
        if current and current["status"] == "queued":
            if (current["mode"],current["perspective"],current["region"]) == (
                data.mode,data.perspective,data.region.lower()):
                return {"status":"already_queued", **_queue_response(con,data.player_id)}
            raise HTTPException(409,"Player is already queued in another matchmaking bucket")
        if current:
            con.execute("DELETE FROM matchmaking_queue WHERE player_id=?",(data.player_id,))
        con.execute(
            "INSERT INTO matchmaking_queue(player_id,mode,perspective,region,enqueued_at,status) "
            "VALUES(?,?,?,?,?,'queued')",
            (data.player_id,data.mode,data.perspective,data.region.lower(),now)
        )
        target = _target(data.mode)
        waiting = con.execute(
            "SELECT player_id FROM matchmaking_queue WHERE mode=? AND perspective=? "
            "AND region=? AND status='queued' ORDER BY enqueued_at LIMIT ?",
            (data.mode,data.perspective,data.region.lower(),target)
        ).fetchall()
        if len(waiting) < target:
            return {"status":"queued", **_queue_response(con,data.player_id)}

        match_id = "m-" + uuid.uuid4().hex[:16]
        humans = [row["player_id"] for row in waiting]
        bot_ids = [f"bot-{uuid.uuid4().hex[:12]}" for _ in range(CLASSIC_BOT_PLAYERS)] if data.mode == "classic" else []
        roster = humans + bot_ids
        total = len(roster)
        con.execute(
            "INSERT INTO live_matches(match_id,mode,perspective,region,status,target_players,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (match_id,data.mode,data.perspective,data.region.lower(),"ready",
             CLASSIC_TARGET_PLAYERS if data.mode == "classic" else TDM_TARGET_PLAYERS,now)
        )
        for index,pid in enumerate(roster):
            is_bot = int(pid.startswith("bot-"))
            con.execute(
                "INSERT INTO live_match_players(match_id,player_id,team_id,joined_at,is_bot) VALUES(?,?,?,?,?)",
                (match_id,pid,index//4,now,is_bot)
            )
            if not is_bot:
                con.execute("UPDATE matchmaking_queue SET status='matched',match_id=? WHERE player_id=?",
                            (match_id,pid))
        if data.mode == "classic":
            _make_deployment(match_id,bot_ids,now)
        return {
            "status":"match_found","match_id":match_id,"mode":data.mode,
            "perspective":data.perspective,"region":data.region.lower(),
            "real_players":len(humans),"bots":len(bot_ids),"total_players":total,
            "plane_deployment": f"/matches/{match_id}/deployment",
            "players": roster
        }


@router.get("/matchmaking/status/{player_id}")
def queue_status(player_id: str):
    _init_matchmaking_db()
    with _connect() as con:
        _clean_expired(con,time.time())
        result = _queue_response(con,player_id)
        return result if result is not None else {"status":"not_queued","player_id":player_id}


@router.delete("/matchmaking/leave/{player_id}")
def leave_queue(player_id: str):
    _init_matchmaking_db()
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row=con.execute("SELECT status FROM matchmaking_queue WHERE player_id=?",(player_id,)).fetchone()
        if row is None:
            return {"status":"not_queued","player_id":player_id}
        if row["status"]=="matched":
            raise HTTPException(409,"Player is already assigned to a match")
        con.execute("DELETE FROM matchmaking_queue WHERE player_id=?",(player_id,))
        return {"status":"left_queue","player_id":player_id}


@router.get("/matches/{match_id}/roster")
def match_roster(match_id: str):
    _init_matchmaking_db()
    with _connect() as con:
        match=con.execute(
            "SELECT match_id,mode,perspective,region,status,target_players,created_at "
            "FROM live_matches WHERE match_id=?",(match_id,)
        ).fetchone()
        if match is None:
            raise HTTPException(404,"Match not found")
        players=con.execute(
            "SELECT player_id,team_id,joined_at,is_bot FROM live_match_players "
            "WHERE match_id=? ORDER BY team_id, is_bot, player_id",(match_id,)
        ).fetchall()
        humans=sum(not row["is_bot"] for row in players)
        bots=sum(bool(row["is_bot"]) for row in players)
        return {"match":dict(match),"player_count":len(players),
                "real_players":humans,"bots":bots,
                "players":[dict(row) for row in players]}


@router.get("/matches/{match_id}/deployment")
def match_deployment(match_id: str):
    _init_matchmaking_db()
    with _connect() as con:
        row=con.execute("SELECT * FROM match_deployments WHERE match_id=?",(match_id,)).fetchone()
        if row is None:
            raise HTTPException(404,"Deployment not found; only Classic matches create a plane route")
        import json
        return {
            "match_id":match_id,
            "plane":{"start":json.loads(row["plane_start_json"]),
                     "end":json.loads(row["plane_end_json"]),
                     "altitude":PLANE_ALTITUDE},
            "bot_jumps":json.loads(row["bot_jumps_json"]),
            "human_players_choose_their_own_jump":True,
            "note":"Bot landing targets are randomized. UE4 must animate the plane, parachutes and bot AI."
        }
