"""Flight fares and schedules from fli (Google Flights), behind a small provider interface.

Facts here come only from the tool and are tagged ``tool:fli``. Fares are indicative: nothing is booked or paid. Google serves
multi-city results through a gated RPC, which fli refuses (``SearchUnsupportedError``, upstream issue #223), so multi-city is
composed from separate one-way searches and labelled as such.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, UTC
from collections.abc import Callable, Sequence

from eventmate.schemas import (
    MAX_DATE_RANGE_DAYS,
    MAX_FLIGHT_OPTIONS,
    MAX_MULTI_CITY_LEGS,
    CheapestDates,
    DateFare,
    FlightOption,
    FlightSearch,
    FlightSegmentInfo,
    MultiCityResult,
)

SOURCE = "tool:fli"
IATA = re.compile(r"[A-Z]{3}")
CABINS = ("economy", "premium_economy", "business", "first")
MAX_ADULTS = 9
MAX_FARE_INR = 10_000_000  # a fare above INR 1 crore is a misread
MAX_BOOKABLE_DAYS = 300  # the date grid is not served beyond about 305 days
MAX_DAYS_AHEAD = 365  # Google Flights sells about this far ahead


def check_date(day: date, today: date) -> None:
    """Refuse dates in the past or so far ahead that date arithmetic or the provider would misbehave."""
    if day < today:
        raise ValueError(f"{day.isoformat()} is in the past")
    if day > today + timedelta(days=MAX_DAYS_AHEAD):
        raise ValueError(f"{day.isoformat()} is too far ahead; searches cover the next {MAX_DAYS_AHEAD} days")


MULTI_CITY_NOTE = (
    "Built from separate one-way searches, one per leg: this is not a multi-city fare, and booking the legs together can price "
    "differently. Google does not serve multi-city results to fli."
)


class TravelError(Exception):
    """The flight tool failed. The message is safe to show; internals stay in ``__cause__``."""


class FlightProvider:
    def search_one_way(
        self, origin: str, destination: str, day: date, adults: int = 1, cabin: str = "economy", max_stops: int | None = None, limit: int = 10
    ) -> FlightSearch:
        raise NotImplementedError

    def cheapest_dates(self, origin: str, destination: str, start: date, end: date, adults: int = 1, cabin: str = "economy", max_stops: int | None = None) -> CheapestDates:
        raise NotImplementedError


def check_range(start: date, end: date, today: date) -> None:
    check_date(start, today)
    if end < start:
        raise ValueError("the last day is before the first day")
    if (end - start).days + 1 > MAX_DATE_RANGE_DAYS:
        raise ValueError(f"search at most {MAX_DATE_RANGE_DAYS} days at a time")
    if end > today + timedelta(days=MAX_BOOKABLE_DAYS):
        raise ValueError(f"{end.isoformat()} is too far ahead; the cheapest-day search covers the next {MAX_BOOKABLE_DAYS} days")


def check_search(origin: str, destination: str, adults: int, cabin: str, limit: int) -> None:
    for code in (origin, destination):
        if not isinstance(code, str) or not IATA.fullmatch(code):
            raise ValueError(f"airport code must be three capital letters, got {code!r}")
    if origin == destination:
        raise ValueError("origin and destination are the same airport")
    if not 1 <= adults <= MAX_ADULTS:
        raise ValueError(f"adults must be 1-{MAX_ADULTS}")
    if cabin not in CABINS:
        raise ValueError(f"cabin must be one of {', '.join(CABINS)}")
    if not 1 <= limit <= MAX_FLIGHT_OPTIONS:
        raise ValueError(f"limit must be 1-{MAX_FLIGHT_OPTIONS}")


class FliProvider(FlightProvider):
    """Wraps ``fli.search.SearchFlights``. fli is an optional dependency and is imported on first use."""

    def __init__(self, search_factory: Callable[[], object] | None = None, date_factory: Callable[[], object] | None = None, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self._factory, self._date_factory, self._now = search_factory, date_factory, now

    def search_one_way(self, origin, destination, day, adults=1, cabin="economy", max_stops=None, limit=10) -> FlightSearch:
        check_search(origin, destination, adults, cabin, limit)
        if max_stops is not None and not 0 <= max_stops <= 2:
            raise ValueError("max_stops must be 0, 1 or 2")
        try:
            from fli.models import Airport, FlightSearchFilters, FlightSegment, MaxStops, PassengerInfo, SeatType, SortBy
            from fli.search import SearchFlights
            from fli.search.exceptions import SearchClientError
        except ImportError as exc:
            raise TravelError("flight search is not installed: pip install the fli package (see docs/CONFIGURATION.md)") from exc
        try:
            frm, to = Airport[origin], Airport[destination]
        except KeyError as exc:
            raise ValueError(f"unknown airport code {exc.args[0]!r}") from exc
        stops = {None: MaxStops.ANY, 0: MaxStops.NON_STOP, 1: MaxStops.ONE_STOP_OR_FEWER, 2: MaxStops.TWO_OR_FEWER_STOPS}[max_stops]
        filters = FlightSearchFilters(
            passenger_info=PassengerInfo(adults=adults),
            flight_segments=[FlightSegment(departure_airport=[[frm, 0]], arrival_airport=[[to, 0]], travel_date=day.isoformat())],
            seat_type=SeatType[cabin.upper()],
            stops=stops,
            sort_by=SortBy.CHEAPEST,
        )
        search = self._factory() if self._factory else SearchFlights()
        try:
            results = search.search(filters, currency="INR") or []
        except SearchClientError as exc:
            raise TravelError(f"Google Flights could not be searched ({type(exc).__name__}); try again shortly") from exc
        except Exception as exc:  # fli or the network misbehaved; never leak internals to the caller
            raise TravelError("the flight search failed unexpectedly") from exc
        warnings: list[str] = []
        options: list[FlightOption] = []
        for r in results:
            try:
                options.append(self._option(search, r, cabin, warnings))
            except Exception:
                warnings.append("one result could not be read and was skipped")
        options.sort(key=lambda o: (o.price_inr is None, o.price_inr or 0, o.duration_min))
        warnings = list(dict.fromkeys(warnings))
        return FlightSearch(
            origin=origin, destination=destination, travel_date=day, options=tuple(options[:limit]), fetched_at=self._now(), source=SOURCE, warnings=tuple(warnings)
        )

    def cheapest_dates(self, origin, destination, start, end, adults=1, cabin="economy", max_stops=None) -> CheapestDates:
        check_search(origin, destination, adults, cabin, 1)
        if max_stops is not None and not 0 <= max_stops <= 2:
            raise ValueError("max_stops must be 0, 1 or 2")
        try:
            from fli.models import Airport, DateSearchFilters, FlightSegment, MaxStops, PassengerInfo, SeatType
            from fli.search import SearchDates
            from fli.search.exceptions import SearchClientError
        except ImportError as exc:
            raise TravelError("flight search is not installed: pip install the fli package (see docs/CONFIGURATION.md)") from exc
        try:
            frm, to = Airport[origin], Airport[destination]
        except KeyError as exc:
            raise ValueError(f"unknown airport code {exc.args[0]!r}") from exc
        stops = {None: MaxStops.ANY, 0: MaxStops.NON_STOP, 1: MaxStops.ONE_STOP_OR_FEWER, 2: MaxStops.TWO_OR_FEWER_STOPS}[max_stops]
        filters = DateSearchFilters(
            passenger_info=PassengerInfo(adults=adults),
            flight_segments=[FlightSegment(departure_airport=[[frm, 0]], arrival_airport=[[to, 0]], travel_date=start.isoformat())],
            seat_type=SeatType[cabin.upper()], stops=stops, from_date=start.isoformat(), to_date=end.isoformat(),
        )
        search = self._date_factory() if self._date_factory else SearchDates()
        try:
            results = search.search(filters, currency="INR") or []
        except SearchClientError as exc:
            raise TravelError(f"Google Flights could not be searched ({type(exc).__name__}); try again shortly") from exc
        except Exception as exc:
            raise TravelError("the flight search failed unexpectedly") from exc
        best: dict[date, int] = {}
        skipped = 0
        for r in results:
            try:
                day, price = r.date[0].date(), r.price
                if (r.currency or "INR") != "INR" or not start <= day <= end or not price or price <= 0 or price > MAX_FARE_INR:
                    raise ValueError
                best[day] = min(best.get(day, MAX_FARE_INR), round(price))
            except Exception:
                skipped += 1
        days = tuple(DateFare(day=d, price_inr=p) for d, p in sorted(best.items()))
        empty = () if days else ("No fares came back for these dates: Google may not sell this route then, or the flight tool may be out of date (see docs/CONFIGURATION.md).",)
        return CheapestDates(origin=origin, destination=destination, from_date=start, to_date=end, days=days,
                             cheapest=min(days, key=lambda f: (f.price_inr, f.day), default=None), fetched_at=self._now(),
                             warnings=empty + ((f"{skipped} price(s) could not be read and were skipped",) if skipped else ()))

    @staticmethod
    def _option(search, r, cabin: str, warnings: list[str]) -> FlightOption:
        price = None
        if r.price is not None:
            if (r.currency or "").upper() == "INR":
                price = round(r.price)
            else:
                warnings.append(f"a fare was published in {r.currency or 'an unknown currency'}, not INR; it is shown without a price")
        segments = tuple(
            FlightSegmentInfo(
                airline_code=leg.airline.name.lstrip("_"), airline_name=str(leg.airline.value), flight_number=str(leg.flight_number),
                from_code=leg.departure_airport.name, to_code=leg.arrival_airport.name,
                departs=leg.departure_datetime, arrives=leg.arrival_datetime, duration_min=leg.duration,
            )
            for leg in r.legs
        )
        try:
            from fli.models import SeatType

            url = search.build_flight_booking_url(r, currency="INR", seat_type=SeatType[cabin.upper()])
        except Exception:
            url = None
        try:
            return FlightOption(price_inr=price, duration_min=r.duration, stops=r.stops, segments=segments, booking_url=url, source=SOURCE)
        except ValueError:
            return FlightOption(price_inr=price, duration_min=r.duration, stops=r.stops, segments=segments, source=SOURCE)  # unusable link is dropped


def search_multi_city(
    provider: FlightProvider, legs: Sequence[tuple[str, str, date]], adults: int = 1, cabin: str = "economy", max_stops: int | None = None, limit: int = 5
) -> MultiCityResult:
    """One search per leg, in order. A failed leg is reported on that leg and removes the total; it is never guessed."""
    legs = list(legs)
    if len(legs) < 2:
        raise ValueError("multi-city needs at least 2 legs")
    if len(legs) > MAX_MULTI_CITY_LEGS:
        raise ValueError(f"multi-city allows at most {MAX_MULTI_CITY_LEGS} legs")
    for i, (o, d, day) in enumerate(legs):
        check_search(o, d, adults, cabin, limit)
        if i and day < legs[i - 1][2]:
            raise ValueError(f"leg {i + 1} departs on {day.isoformat()}, not before leg {i} ({legs[i - 1][2].isoformat()})")
    results: list[FlightSearch] = []
    for o, d, day in legs:
        try:
            results.append(provider.search_one_way(o, d, day, adults=adults, cabin=cabin, max_stops=max_stops, limit=limit))
        except TravelError as exc:
            results.append(FlightSearch(origin=o, destination=d, travel_date=day, fetched_at=datetime.now(UTC), source=SOURCE, error=str(exc)[:300]))
    cheapest: list[int | None] = []
    for r in results:
        prices = [x.price_inr for x in r.options if x.price_inr is not None]
        cheapest.append(min(prices) if prices else None)
    incomplete = tuple(i for i, c in enumerate(cheapest) if c is None)
    return MultiCityResult(legs=tuple(results), total_cheapest_inr=None if incomplete else sum(cheapest), incomplete_legs=incomplete, note=MULTI_CITY_NOTE)
