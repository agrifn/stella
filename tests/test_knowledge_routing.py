"""The knowledge module routes a single 'info' intent to buy / craft / stats by
regex. These are the rules that decide whether a query hits UEX, the blueprint
index, or the stats pool - exactly the behaviour we debugged live, so pin it."""
from server.knowledge import _clean_query, _is_buy_query, _is_craft_query, _place


def test_buy_queries():
    assert _is_buy_query("where can I buy a P4-AR")
    assert _is_buy_query("how much is a demeco")
    assert _is_buy_query("how much does a P4-AR cost")
    assert _is_buy_query("cheapest scourge railgun")
    assert _is_buy_query("where do I find a quantum drive")


def test_not_buy_queries():
    # stat questions must NOT be treated as buy queries
    assert not _is_buy_query("how much armor does the guardian have")
    assert not _is_buy_query("how much shields on a cutlass")
    assert not _is_buy_query("what is the A03 fire rate")
    assert not _is_buy_query("where is crusader")  # location, not a purchase


def test_craft_queries():
    assert _is_craft_query("what do I need to craft an omnisky III")
    assert _is_craft_query("how do I make a P4-AR")
    assert _is_craft_query("whats the recipe for an Arrowhead")
    assert _is_craft_query("what are the ingredients for X")


def test_not_craft_queries():
    assert not _is_craft_query("what is the A03 fire rate")
    assert not _is_craft_query("where can I buy a P4-AR")


def test_clean_query_strips_filler():
    assert _clean_query("where can I buy a P4-AR") == "p4 ar"
    assert _clean_query("how much is a demeco") == "demeco"
    assert _clean_query("what do I need to craft an omnisky III") == "omnisky iii"


def test_place_most_specific_first():
    rec = {"space_station_name": "Baijini Point", "planet_name": "Crusader",
           "star_system_name": "Stanton", "orbit_name": "Crusader"}
    assert _place(rec) == "Baijini Point, Crusader, Stanton"


def test_place_dedupes_orbit_equal_planet():
    rec = {"space_station_name": None, "city_name": None, "outpost_name": None,
           "orbit_name": "Hurston", "planet_name": "Hurston", "star_system_name": "Stanton"}
    assert _place(rec) == "Hurston, Stanton"
