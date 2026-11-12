import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "test_game.db")
    main.init_db()
    with TestClient(main.app) as test_client:
        yield test_client


def create_player(client, player_id="test-player"):
    response = client.post(f"/players/{player_id}")
    assert response.status_code == 200
    return player_id


def test_health_and_systems(client):
    assert client.get("/health").json()["status"] == "healthy"
    systems = client.get("/systems").json()["systems"]
    assert systems["healing_boost"] == "available"


def test_weapon_ammo_fire_reload(client):
    player_id = create_player(client)
    response = client.post(
        f"/player/{player_id}/weapon/primary_1",
        json={"weapon_id": "M416", "ammo": 10, "reserve_ammo": 40},
    )
    assert response.status_code == 200
    fired = client.post(f"/player/{player_id}/weapon/primary_1/fire?shots=3")
    assert fired.status_code == 200
    assert fired.json()["ammo_in_magazine"] == 7
    reloaded = client.post(f"/player/{player_id}/weapon/primary_1/reload")
    assert reloaded.status_code == 200
    assert reloaded.json()["ammo_in_magazine"] == 30


def test_healing_and_armor_damage(client):
    player_id = create_player(client)
    assert client.post(f"/player/{player_id}/item", json={
        "item_id": "med_kit", "quantity": 1
    }).status_code == 200
    assert client.put(f"/player/{player_id}/health?health=40").status_code == 200
    healed = client.post(f"/player/{player_id}/heal", json={
        "item_id": "med_kit", "quantity": 1
    })
    assert healed.status_code == 200
    assert healed.json()["health"] == 100
    armor = client.put(f"/player/{player_id}/armor", json={
        "helmet_level": 2, "vest_level": 2
    })
    assert armor.status_code == 200
    damage = client.post(f"/player/{player_id}/damage", json={
        "damage": 50, "hit_zone": "body"
    })
    assert damage.status_code == 200
    assert damage.json()["health_damage"] < 50


def test_vehicle_and_match_flow(client):
    player_id = create_player(client)
    assert client.put(f"/player/{player_id}/vehicle", json={
        "vehicle_id": "buggy", "fuel": 80
    }).status_code == 200
    fuel = client.post(f"/player/{player_id}/vehicle/fuel?amount=10")
    assert fuel.json()["vehicle"]["fuel"] == 70
    assert client.delete(f"/player/{player_id}/vehicle").status_code == 200
    assert client.post(f"/player/{player_id}/match/join?match_id=match-001").status_code == 200
    roster = client.get("/match/match-001/players").json()
    assert roster["players"] == [player_id]
    assert client.post(f"/player/{player_id}/match/leave").status_code == 200


def test_loot_spawn_and_pickup(client):
    player_id = create_player(client)
    spawned = client.post("/loot", json={
        "loot_id": "loot-001", "item_id": "5.56mm",
        "quantity": 30, "weight": 1
    })
    assert spawned.status_code == 200
    picked = client.post(f"/player/{player_id}/loot/loot-001/pickup")
    assert picked.status_code == 200
    inventory = client.get(f"/player/{player_id}/inventory").json()
    assert any(item["item_id"] == "5.56mm" and item["quantity"] == 30
               for item in inventory["items"])
