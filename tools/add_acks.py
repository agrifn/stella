import json
from pathlib import Path
ACKS = {
    "decouple_toggle":"Decoupling.", "cruise_control_toggle":"Cruise control.",
    "vtol_toggle":"V-TOL.", "landing_gear":"Landing gear.",
    "request_landing":"Requesting landing.", "nav_mode":"Quantum mode.",
    "power_toggle_all":"All power.", "systems_ready":"Systems ready.",
    "weapons_power_toggle":"Weapons power.", "shields_power_toggle":"Shields power.",
    "thrusters_power_toggle":"Thruster power.", "reset_power":"Power balanced.",
    "weapons_power_max":"Weapons to max.", "engines_power_max":"Engines to max.",
    "shields_power_max":"Shields to max.", "weapons_power_inc":"More weapons.",
    "engines_power_inc":"More engines.", "shields_power_inc":"More shields.",
    "weapons_power_dec":"Less weapons.", "engines_power_dec":"Less engines.",
    "shields_power_dec":"Less shields.", "lower_weapons_min":"Weapons minimum.",
    "lower_engine_min":"Engines minimum.", "lower_shields_min":"Shields minimum.",
    "unlock_target":"Target released.", "gimbal_cycle":"Gimbal mode.",
    "decoy_burst":"Countermeasures away.", "scan_mode":"Scanning.",
    "ping":"Pinging.", "mfd_cycle_forward":"Next display.",
    "mfd_cycle_back":"Previous display.", "exit_seat":"Leaving the seat.",
    "look_behind":"Looking behind.", "headlights":"Lights.",
    "mobiglas":"Mobiglas.", "comms":"Comms.", "starmap":"Starmap.",
    "camera_cycle":"Camera.", "eject":"Ejecting.", "self_destruct":"Self destruct.",
}
p = Path("config/keybinds.json")
d = json.loads(p.read_text(encoding="utf-8"))
kb = d["keybinds"]
missing = []
for intent, spec in kb.items():
    spec["ack"] = ACKS.get(intent) or intent.replace("_", " ").capitalize() + "."
    if intent not in ACKS: missing.append(intent)
p.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
print(f"added acks to {len(kb)} commands; defaulted: {missing}")
