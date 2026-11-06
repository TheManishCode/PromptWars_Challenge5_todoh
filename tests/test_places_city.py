"""Places matches a name before a place: "Udupi Caterers" in Bengaluru, or a city it cannot find answered with another city's businesses. A listing plainly in another city is left out."""

import json

import pytest

from eventmate.integrations.places import PlacesSource, in_other_city


@pytest.mark.parametrize("address,city,elsewhere", [
    ("Alevoor - Manipal Rd, Padu Alevoor, Udupi, Karnataka 574118", "Udupi", False),
    ("Kothanur, Bengaluru, Karnataka 560077", "Udupi", True),
    ("Korangrapady, Karnataka 574118", "Udupi", False),  # names no known city: kept, it may be a locality
    ("Kuvempunagara, Mysuru, Karnataka 570009", "Mysore", False),  # one city, two spellings
    ("Nandini Layout, Mysore, Karnataka", "Mysuru", False),
    ("Whitefield, Karnataka", "Bengaluru", False),
    ("Udupi Rd, Mangaluru", "Udupi", False),  # names the city asked for as well: kept
    ("MG Road, Pune", "Mysuru", True),
    ("Pune, Maharashtra", "Mars", True),  # a city nothing knows is not given another city's businesses
    ("MG Road", "Mysuru", False),  # a locality word is in many cities and decides nothing
    (None, "Mysuru", False),
])
def test_a_listing_is_left_out_only_when_it_is_plainly_in_another_city(address, city, elsewhere):
    assert in_other_city(address, city) is elsewhere


def test_the_search_leaves_them_out_and_says_so():
    def reply(url, headers, data, timeout):
        return json.dumps({"places": [
            {"id": "ChIJaaaaaaaa11111", "displayName": {"text": "Udupi Caterers"}, "formattedAddress": "Manipal, Udupi, Karnataka 576104", "rating": 4.8, "userRatingCount": 50},
            {"id": "ChIJbbbbbbbb22222", "displayName": {"text": "Udupi Golden Caterers"}, "formattedAddress": "Kothanur, Bengaluru, Karnataka 560077", "rating": 5.0, "userRatingCount": 100},
        ]}).encode()

    out = PlacesSource(api_key="k" * 30, transport=reply).search("caterer", "Udupi")
    assert [p.name for p in out.places] == ["Udupi Caterers"]
    assert any("1 listing(s) in other cities were left out" in w for w in out.warnings)
