from seatfinder.catalog import Catalog


def test_resolves_default_movie_and_city_aliases():
    catalog = Catalog()
    movie = catalog.resolve_movie("doomsday")
    assert movie.title == "Avengers: Doomsday"
    assert movie.default is True
    assert movie.bms_event_code == "ET00439706"
    assert movie.district_content_id == "176735"

    city = catalog.resolve_city("bangalore")
    assert city.name == "Bengaluru"
    assert city.bms_slug == "bengaluru"
    assert city.district_city_key == "bengaluru"


def test_unknown_movie_falls_back_to_a_partial_ref():
    catalog = Catalog()
    movie = catalog.resolve_movie("Some New Film")
    assert movie.title == "Some New Film"
    assert movie.default is False
    assert movie.bms_event_code is None
