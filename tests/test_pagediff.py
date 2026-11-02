"""Page differences (architecture L9, ported from openmuse's page-diff): what is news and what changes by itself."""

import time

from eventmate.followthrough.pagediff import count, describe, diff_page, page_lines, without_relative_times


def d(a, b):
    return diff_page(page_lines(a), page_lines(b))


def test_lines_are_collapsed_distinct_and_capped():
    assert page_lines("  a   b \n\n a b\nc\n") == ["a b", "c"]
    assert len(page_lines("x" * 5000)[0]) == 300
    assert len(page_lines("\n".join(f"line {i}" for i in range(5000)))) == 2000


def test_a_relative_time_ticking_on_is_not_news():
    assert d("Venue hall posted 2 days ago", "Venue hall posted 3 days ago").empty()
    assert d("updated 58 minutes ago", "updated an hour ago").empty()
    assert without_relative_times("posted one week ago; 5 mins ago") == "posted <time>; <time>"


def test_a_price_change_is_news_and_is_an_update_not_an_add_and_a_removal():
    diff = d("Package A: Rs 30,000 per day\nParking", "Package A: Rs 28,000 per day\nParking")
    assert diff.updated == ("Package A: Rs 28,000 per day",) and diff.added == () and diff.removed == ()


def test_new_and_removed_lines_are_listed():
    diff = d("Open Mon-Sat\nCapacity 200", "Open Mon-Sat\nDecember dates available")
    assert diff.added == ("December dates available",) and diff.removed == ("Capacity 200",) and diff.updated == ()


def test_one_leftover_line_cannot_account_for_two_changes():
    diff = d("Price 10\n", "Price 20\nPrice 30\n")
    assert len(diff.updated) == 1 and len(diff.added) == 1 and diff.removed == ()


def test_what_is_listed_keeps_the_pages_order():
    diff = d("z gone\na gone\nkeep", "keep\nq new\nb new")
    assert diff.removed == ("z gone", "a gone") and diff.added == ("q new", "b new")


def test_an_unchanged_page_has_no_diff_and_says_nothing():
    diff = d("a\nb", "a\nb")
    assert diff.empty() and describe(diff) == "" and count(diff) == ""


def test_the_summaries_are_short_and_say_how_many_more():
    diff = d("", "\n".join(f"item {chr(97 + i)}" for i in range(12)))
    text = describe(diff)
    assert text.startswith("New:") and "+4 more" in text and count(diff) == "12 lines changed (12 new)"
    assert count(d("a", "a\nb")) == "1 line changed (1 new)"


def test_hostile_pages_are_cheap_to_compare():
    evil = "\n".join(["9" * 290] * 2000) + "\n" + ("a " * 100_000)
    started = time.perf_counter()
    diff_page(page_lines(evil), page_lines(evil + "\nnew"))
    for s in ("an " * 50_000 + "hours ago", "1 " * 80_000, "a" * 500_000):
        without_relative_times(s)
        page_lines(s)
    assert time.perf_counter() - started < 5
