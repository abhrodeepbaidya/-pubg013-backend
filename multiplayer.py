"""Realtime multiplayer session layer.

WebSocket room relay with server-side movement bounds/speed checks and
persisted player snapshots. This is a prototype session worker in the API
process, not a horizontally scalable dedicated game-server fleet.
"""
from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["multiplayer"])
DB_PATH = Path(__file__).resolve().parent / "game.db"
MAP_HALF_EXTENT = 4000.0
MAX_MOVE_SPEED = 900.0  # Unreal centimetres per second; tune to movement/vehicle state.
MAX_MESSAGE_BYTES = 4096
MAX_CLIENT_RATE = 30  # messages/second/player


def _db():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    return con


def _init():
    with _db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS multiplayer_player_states (
                match_id TEXT NOT NULL,
                player_id TEXT NOT NULL,
                x REAL NOT NULL DEFAULT 0,
                y REAL NOT NULL DEFAULT 0,
                z REAL NOT NULL DEFAULT 0,
                yaw REAL NOT NULL DEFAULT 0,
                health INTEGER NOT NULL DEFAULT 100,
                alive INTEGER NOT NULL DEFAULT 1,
                updated_at REAL NOT NULL,
                PRIMARY KEY(match_id, player_id)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS multiplayer_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT NOT NULL,
                player_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)


class Room:
    def __init__(self):
        self.clients: dict[str, WebSocket] = {}
        self.states: dict[str, dict[str, Any]] = {}
        self.lock = asyncio.Lock()


_ROOMS: dict[str, Room] = defaultdict(Room)


def _member(match_id: str, player_id: str) -> bool:
    with _db() as con:
        return con.execute(
            "SELECT 1 FROM live_match_players WHERE match_id=? AND player_id=?",
            (match_id, player_id),
        ).fetchone() is not None


def _load_state(match_id: str, player_id: str) -> dict[str, Any]:
    with _db() as con:
        row = con.execute(
            "SELECT x,y,z,yaw,health,alive,updated_at FROM multiplayer_player_states "
            "WHERE match_id=? AND player_id=?", (match_id, player_id)
        ).fetchone()
        if row is None:
            state = {"player_id": player_id, "x": 0.0, "y": 0.0, "z": 0.0,
                     "yaw": 0.0, "health": 100, "alive": True, "updated_at": time.time()}
            con.execute(
                "INSERT INTO multiplayer_player_states(match_id,player_id,x,y,z,yaw,health,alive,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (match_id, player_id, 0, 0, 0, 0, 100, 1, state["updated_at"]),
            )
            return state
        return {"player_id": player_id, "x": row["x"], "y": row["y"], "z": row["z"],
                "yaw": row["yaw"], "health": row["health"], "alive": bool(row["alive"]),
                "updated_at": row["updated_at"]}


def _save_state(match_id: str, state: dict[str, Any]):
    with _db() as con:
        con.execute(
            "INSERT INTO multiplayer_player_states(match_id,player_id,x,y,z,yaw,health,alive,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(match_id,player_id) DO UPDATE SET "
            "x=excluded.x,y=excluded.y,z=excluded.z,yaw=excluded.yaw,health=excluded.health,"
            "alive=excluded.alive,updated_at=excluded.updated_at",
            (match_id, state["player_id"], state["x"], state["y"], state["z"],
             state["yaw"], state["health"], int(state["alive"]), state["updated_at"]),
        )


async def _broadcast(room: Room, message: dict[str, Any], exclude: str | None = None):
    stale = []
    for pid, ws in list(room.clients.items()):
        if pid == exclude:
            continue
        try:
            await ws.send_json(message)
        except Exception:
            stale.append(pid)
    for pid in stale:
        room.clients.pop(pid, None)


@router.get("/matches/{match_id}/multiplayer/state")
def multiplayer_state(match_id: str):
    _init()
    with _db() as con:
        rows = con.execute(
            "SELECT player_id,x,y,z,yaw,health,alive,updated_at "
            "FROM multiplayer_player_states WHERE match_id=?",
            (match_id,),
        ).fetchall()
    return {"match_id": match_id, "players": [dict(r) for r in rows]}


@router.websocket("/ws/matches/{match_id}")
async def multiplayer_socket(websocket: WebSocket, match_id: str, player_id: str):
    _init()
    if not _member(match_id, player_id):
        await websocket.close(code=1008, reason="Player is not in this match roster")
        return

    await websocket.accept()
    room = _ROOMS[match_id]
    async with room.lock:
        old = room.clients.get(player_id)
        if old is not None:
            try:
                await old.close(code=4001, reason="Player reconnected")
            except Exception:
                pass
        room.clients[player_id] = websocket
        state = room.states.setdefault(player_id, _load_state(match_id, player_id))
        await websocket.send_json({
            "type": "welcome", "match_id": match_id, "player_id": player_id,
            "server_time": time.time(), "players": list(room.states.values()),
        })
        await _broadcast(room, {"type": "player_joined", "player": state}, exclude=player_id)

    last_message = 0.0
    message_count = 0
    window_start = time.monotonic()
    try:
        while True:
            raw = await websocket.receive_text()
            if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
                await websocket.close(code=1009, reason="Message too large")
                break
            now_mono = time.monotonic()
            if now_mono - window_start >= 1:
                window_start, message_count = now_mono, 0
            message_count += 1
            if message_count > MAX_CLIENT_RATE:
                await websocket.close(code=1008, reason="Rate limit exceeded")
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "code": "invalid_json"})
                continue
            if not isinstance(msg, dict):
                await websocket.send_json({"type": "error", "code": "invalid_message"})
                continue

            kind = msg.get("type")
            if kind == "ping":
                await websocket.send_json({"type": "pong", "server_time": time.time()})
                continue
            if kind == "move":
                coords = [msg.get(k) for k in ("x", "y", "z", "yaw")]
                if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in coords):
                    await websocket.send_json({"type": "error", "code": "invalid_coordinates"})
                    continue
                x, y, z, yaw = map(float, coords)
                if abs(x) > MAP_HALF_EXTENT or abs(y) > MAP_HALF_EXTENT or z < -500 or z > 3000:
                    await websocket.send_json({"type": "error", "code": "out_of_bounds"})
                    continue
                state = room.states[player_id]
                elapsed = max(0.001, time.time() - state["updated_at"])
                distance = math.dist((x, y, z), (state["x"], state["y"], state["z"]))
                if distance > MAX_MOVE_SPEED * elapsed + 150:
                    await websocket.send_json({"type": "error", "code": "movement_speed_exceeded"})
                    continue
                state.update({"x": x, "y": y, "z": z, "yaw": yaw % 360, "updated_at": time.time()})
                _save_state(match_id, state)
                await _broadcast(room, {"type": "player_moved", "player": state}, exclude=player_id)
            elif kind in {"emote", "weapon_state", "animation"}:
                payload = msg.get("payload", {})
                if not isinstance(payload, dict) or len(json.dumps(payload)) > 1024:
                    await websocket.send_json({"type": "error", "code": "invalid_payload"})
                    continue
                with _db() as con:
                    con.execute(
                        "INSERT INTO multiplayer_events(match_id,player_id,event_type,payload_json,created_at) "
                        "VALUES(?,?,?,?,?)",
                        (match_id, player_id, kind, json.dumps(payload), time.time()),
                    )
                await _broadcast(room, {"type": kind, "player_id": player_id, "payload": payload},
                                 exclude=player_id)
            else:
                await websocket.send_json({"type": "error", "code": "unsupported_message_type"})
    except WebSocketDisconnect:
        pass
    finally:
        async with room.lock:
            if room.clients.get(player_id) is websocket:
                room.clients.pop(player_id, None)
                await _broadcast(room, {"type": "player_left", "player_id": player_id})
