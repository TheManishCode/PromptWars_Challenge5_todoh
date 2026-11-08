"""Every first request now says "Reply STOP and we will not write again", so a reply that says stop the way people actually write it must be read as one:
with a signature under it, with a please or a thanks, or above the quoted request. Read through the same path as a real reply (clean, then analyse).
And the other way: a price reply, or a sentence that merely contains the word, is not a stop."""
import pytest

from eventmate.messaging.channels import clean_reply_text
from eventmate.messaging.conversations import analyze_reply


def reads_as_stop(raw):
    return analyze_reply(clean_reply_text(raw)[0], ()).asks_to_stop


@pytest.mark.parametrize("raw", [
    "STOP",
    "Stop\n\nSent from my iPhone",
    "STOP\n\nOn Mon, 9 Nov 2026 at 10:02, Asha <asha@x.in> wrote:\n> Reply STOP and we will not write again.",
    "STOP\n-- \nRavi\nSri Hari Caterers\n+91 98450 00000",
    "STOP\n--\nRavi\nSri Hari Caterers",
    "Stop\nRavi, Sri Hari Caterers",
    "STOP. Thanks",
    "Please stop",
    "stop please",
    "Stop!\n\nRegards,\nRavi",
])
def test_a_stop_written_the_way_people_write_it_is_a_stop(raw):
    assert reads_as_stop(raw), raw


@pytest.mark.parametrize("raw", [
    "Price per plate (INR): 650\nAvailable on the date (yes/no): yes",
    "We cannot stop by on Monday, but the price is 650 per plate",
    "Stop the music at 10 pm is our rule. Price for the day (INR): 20,000",
    "Available on the date (yes/no): yes\n\n> Reply STOP and we will not write again.",
    "Non-stop service included. Price per plate (INR): 700",
])
def test_a_reply_that_only_contains_the_word_is_not_a_stop(raw):
    assert not reads_as_stop(raw), raw
