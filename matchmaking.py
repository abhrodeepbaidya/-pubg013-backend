"""Persistent matchmaking queue for the Battle Royale backend.

This module provides queue orchestration only. It does not simulate a game
server or validate client movement/combat; those require an authoritative
real-time session worker.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(tags=["matchmaking"])
DB_PATH = Path(__file__).resolve().parent / "game.db"
QUEUE_TTL_SECONDS = 180
CLASSIC_TARGET_PLAYERS = 100
TDM_TARGET_PLAYERS = 8


def _connect():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    return con


def _init_matchmaking_db():
    with _connect() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS matchmaking_queue (
                player_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                perspective TEXT NOT NULL,
                region TEXT NOT NULL,
                enqueued_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                match_id TEXT
            )
        """)
        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_matchmaking_bucket
            ON matchmaking_queue(mode, perspective, region, status, enqueued_at)
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS live_matches (
                match_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                perspective TEXT NOT NULL,
                region TEXT NOT NULL,
                status TEXT NOT NULL,
                target_players INTEGER NOT NULL,
                created_at REAL NOT NULL,
                started_at REAL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS live_match_players (
                match_id TEXT NOT NULL,
                player_id TEXT NOT NULL UNIQUE,
                team_id INTEGER,
                joined_at REAL NOT NULL,
                PRIMARY KEY(match_id, player_id)
            )
        """)


class QueueJoinInput(BaseModel):
    player_id: str = Field(min_length=1, max_length=64)
    mode: Literal["classic", "tdm"] = "classic"
    perspective: Literal["tpp", "fpp"] = "tpp"
    region: str = Field(default="asia", min_length=2, max_length=16)


def _target(mode: str) -> int:
    return CLASSIC_TARGET_PLAYERS if mode == "classic" else TDM_TARGET_PLAYERS


def _clean_expired(con: sqlite3.Connection, now: float):
    con.execute(
        "DELETE FROM matchmaking_queue WHERE status='queued' AND enqueued_at < ?",
        (now - QUEUE_TTL_SECONDS,),
    )


def _queue_response(con: sqlite3.Connection, player_id: str):
    row = con.execute(
        "SELECT player_id,mode,perspective,region,enqueued_at,status,match_id "
        "FROM matchmaking_queue WHERE player_id=?", (player_id,)
    ).fetchone()
    if row is None:
        return None
    bucket_count = con.execute(
        "SELECT COUNT(*) FROM matchmaking_queue WHERE mode=? AND perspective=? "
        "AND region=? AND status='queued'",
        (row["mode"], row["perspective"], row["region"]),
    ).fetchone()[0]
    return {
        "player_id": row["player_id"],
        "mode": row["mode"],
        "perspective": row["perspective"],
        "region": row["region"],
        "status": row["status"],
        "match_id": row["match_id"],
        "queued_seconds": max(0, int(time.time() - row["enqueued_at"])),
        "players_in_queue": bucket_count,
        "players_required": _target(row["mode"]),
    }


@router.post("/matchmaking/join")
def join_queue(data: QueueJoinInput):
    _init_matchmaking_db()
    now = time.time()
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        _clean_expired(con, now)
        # Do not allow nonexistent player IDs into the queue.
        player = con.execute(
            "SELECT 1 FROM players WHERE player_id=?", (data.player_id,)
        ).fetchone()
        if player is None:
            raise HTTPException(404, "Player not found; register or create the player first")
        current = con.execute(
            "SELECT status,match_id,mode,perspective,region FROM matchmaking_queue "
            "WHERE player_id=?", (data.player_id,)
        ).fetchone()
        if current and current["status"] == "queued":
            if (current["mode"], current["perspective"], current["region"]) == (
                data.mode, data.perspective, data.region.lower()
            ):
                return {"status": "already_queued", **_queue_response(con, data.player_id)}
            raise HTTPException(409, "Player is already queued in another matchmaking bucket")
        if current:
            con.execute("DELETE FROM matchmaking_queue WHERE player_id=?", (data.player_id,))

        con.execute(
            "INSERT INTO matchmaking_queue(player_id,mode,perspective,region,enqueued_at,status) "
            "VALUES(?,?,?,?,?,'queued')",
            (data.player_id, data.mode, data.perspective, data.region.lower(), now),
        )
        target = _target(data.mode)
        waiting = con.execute(
            "SELECT player_id FROM matchmaking_queue WHERE mode=? AND perspective=? "
            "AND region=? AND status='queued' ORDER BY enqueued_at LIMIT ?",
            (data.mode, data.perspective, data.region.lower(), target),
        ).fetchall()

        if len(waiting) >= target:
            match_id = "m-" + uuid.uuid4().hex[:16]
            player_ids = [item["player_id"] for item in waiting]
            con.execute(
                "INSERT INTO live_matches(match_id,mode,perspective,region,status,target_players,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (match_id, data.mode, data.perspective, data.region.lower(), "ready", target, now),
            )
            for index, queued in enumerate(player_ids):
                team_id = (index // 2) if data.mode == "classic" else (index // 4)
                con.execute(
                    "INSERT INTO live_match_players(match_id,player_id,team_id,joined_at) VALUES(?,?,?,?)",
                    (match_id, queued, team_id, now),
                )
                con.execute(
                    "UPDATE matchmaking_queue SET status='matched',match_id=? WHERE player_id=?",
                    (match_id, queued),
                )
            return {
                "status": "match_found",
                "match_id": match_id,
                "mode": data.mode,
                "perspective": data.perspective,
                "region": data.region.lower(),
                "target_players": target,
                "players": player_ids,
            }

        return {"status": "queued", **_queue_response(con, data.player_id)}


@router.get("/matchmaking/status/{player_id}")
def queue_status(player_id: str):
    _init_matchmaking_db()
    with _connect() as con:
        _clean_expired(con, time.time())
        result = _queue_response(con, player_id)
        if result is None:
            return {"status": "not_queued", "player_id": player_id}
        return result


@router.delete("/matchmaking/leave/{player_id}")
def leave_queue(player_id: str):
    _init_matchmaking_db()
    with _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT status,match_id FROM matchmaking_queue WHERE player_id=?",
            (player_id,),
        ).fetchone()
        if row is None:
            return {"status": "not_queued", "player_id": player_id}
        if row["status"] == "matched":
            raise HTTPException(409, "Player is already assigned to a match")
        con.execute("DELETE FROM matchmaking_queue WHERE player_id=?", (player_id,))
        return {"status": "left_queue", "player_id": player_id}


@router.get("/matches/{match_id}/roster")
def match_roster(match_id: str):
    _init_matchmaking_db()
    with _connect() as con:
        match = con.execute(
            "SELECT match_id,mode,perspective,region,status,target_players,created_at "
            "FROM live_matches WHERE match_id=?", (match_id,)
        ).fetchone()
        if match is None:
            raise HTTPException(404, "Match not found")
        players = con.execute(
            "SELECT player_id,team_id,joined_at FROM live_match_players "
            "WHERE match_id=? ORDER BY joined_at,player_id", (match_id,)
        ).fetchall()
        return {
            "match": dict(match),
            "player_count": len(players),
            "players": [dict(player) for player in players],
        }
