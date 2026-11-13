"""Classic-era-inspired weapon fire/audio metadata for the UE4 client.

RPM values are provisional tuning estimates, not verified PUBG Mobile 0.13.0
extracted data. Fire modes are weapon-specific. Sound event names are integration
hooks; actual licensed/authorized audio assets must be supplied by the client.
"""
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["weapon-profiles"])

# weapon_id: rounds per minute, fire modes, reload seconds, audio event keys
PROFILES = {
    "M416": (700, ["single", "auto"], 2.1),
    "AKM": (600, ["single", "auto"], 2.9),
    "SCAR-L": (600, ["single", "auto"], 2.2),
    "M16A4": (800, ["single", "burst"], 2.2),
    "AUG A3": (600, ["single", "auto"], 3.0),
    "Groza": (750, ["single", "auto"], 3.0),
    "QBZ": (600, ["single", "auto"], 2.2),
    "UMP9": (650, ["single", "auto"], 3.1),
    "UMP45": (600, ["single", "auto"], 3.1),
    "UZI": (1200, ["auto"], 3.1),
    "Vector": (1100, ["single", "auto"], 2.2),
    "Tommy Gun": (700, ["single", "auto"], 3.45),
    "Kar98k": (0, ["single"], 2.3),
    "M24": (0, ["single"], 1.8),
    "AWM": (None, ["single"], 4.6),
    "Win94": (None, ["single"], 0.6),
    "Mini14": (None, ["single"], 3.0),
    "SKS": (None, ["single"], 2.9),
    "Mk14": (750, ["single", "auto"], 3.0),
    "QBU": (None, ["single"], 3.0),
    "DP-28": (550, ["single", "auto"], 5.5),
    "M249": (750, ["auto"], 5.7),
    "S1897": (None, ["single"], 4.0),
    "S686": (None, ["single"], 3.0),
    "S12K": (None, ["single"], 3.0),
    "P92": (None, ["single"], 1.8),
    "P18C": (1000, ["single", "auto"], 2.0),
    "P1911": (None, ["single"], 1.9),
    "R1895": (None, ["single"], 2.0),
    "R45": (None, ["single"], 3.2),
    "Sawed-off": (None, ["single"], 2.1),
    "Crossbow": (None, ["single"], 3.7),
}

def _profile(weapon_id: str, row: tuple):
    rpm, modes, reload_seconds = row
    automatic = "auto" in modes
    return {
        "weapon_id": weapon_id,
        "fire_rate_rpm": rpm if rpm else None,
        "fire_interval_ms": round(60000 / rpm, 2) if rpm else None,
        "fire_modes": modes,
        "default_fire_mode": "auto" if "auto" in modes else ("burst" if "burst" in modes else "single"),
        "supports_single": "single" in modes,
        "supports_auto": "auto" in modes,
        "supports_burst": "burst" in modes,
        "reload_seconds": reload_seconds,
        "audio_events": {
            "fire": f"weapon.{weapon_id}.fire",
            "tail": f"weapon.{weapon_id}.tail",
            "reload": f"weapon.{weapon_id}.reload",
            "dry_fire": f"weapon.{weapon_id}.dry_fire",
        },
        "looping_fire_audio": automatic,
        "audio_asset_status": "event_keys_only_audio_files_not_in_backend",
        "rate_status": "provisional_tuning_estimate" if rpm else "manual_or_action_gated_weapon",
    }

@router.get("/weapons/profiles")
def all_weapon_profiles():
    return {
        "count": len(PROFILES),
        "source_note": "Classic-era-inspired provisional metadata; not official PUBG 0.13.0 extracted weapon data.",
        "weapons": [_profile(name, row) for name, row in PROFILES.items()],
    }

@router.get("/weapons/{weapon_id}/profile")
def weapon_profile(weapon_id: str):
    row = PROFILES.get(weapon_id)
    if row is None:
        raise HTTPException(404, "Weapon profile not found")
    return _profile(weapon_id, row)
