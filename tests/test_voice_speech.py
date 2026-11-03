"""What is said aloud is the checked answer rewritten, never added to."""

import pytest

from eventmate.voice.speech import (
    MORE, answer_values, spoken_answer, spoken_values, speak_numbers, split_sentences, strip_markup, verify_spoken,
)

ANSWER = (
    "Your total is **INR 1,39,000** of the INR 2,00,000 budget (70%). See [the plan](http://x.example/p) for details.\n\n"
    "- The venue is Orchid Banquet.\n- The caterer is Royal Rasoi, quoted at Rs. 650 per plate.\nIt is on 12 December 2026 for 120 guests, e.g. in Pune."
)


@pytest.mark.parametrize("text,said", [
    ("INR 1,39,000", "1 lakh 39 thousand rupees"), ("INR 61,000", "61 thousand rupees"), ("Rs. 5,00,000", "5 lakh rupees"), ("₹2,50,00,000", "2 crore 50 lakh rupees"),
    ("INR 999", "999 rupees"), ("INR 1,000", "1 thousand rupees"), ("Rs 650 per plate", "650 rupees per plate"), ("INR 1500.50", "1500.50 rupees"),
    ("about 1,39,000 people", "about 1 lakh 39 thousand people"), ("120 guests", "120 guests"), ("on 12 December 2026", "on 12 December 2026"), ("70%", "70 percent"), ("7 %", "7 percent"),
    ("room 1,200 sq ft", "room 1,200 sq ft"),  # a number under a lakh that is not money is left to the speech engine
])
def test_numbers_are_written_the_way_people_say_them(text, said):
    assert speak_numbers(text) == said


def test_markup_tables_links_and_addresses_are_dropped_but_the_words_stay():
    out = strip_markup("## Plan\n**Bold** and *soft* and `code` [the plan](http://x.example/a) at https://y.example/z or www.z.example.\n| a | b |\n|---|---|\n| 1 | 2 |")
    assert "http" not in out and "www" not in out and "*" not in out and "`" not in out and "#" not in out and "|" not in out and "---" not in out
    assert "Bold and soft and code the plan at" in out and "a, b" in out
    assert strip_markup("- one\n* two\n1. three\n2) four") == "one\ntwo\nthree\nfour"


def test_sentences_split_on_ends_and_lines_but_not_decimals_or_abbreviations():
    assert split_sentences("Rs. 650 per plate, e.g. veg. It costs 1.5 lakh. Done!") == ["Rs. 650 per plate, e.g. veg.", "It costs 1.5 lakh.", "Done!"]
    assert split_sentences("Mr. Rao called. Dr. Mehta agreed.") == ["Mr. Rao called.", "Dr. Mehta agreed."]
    assert split_sentences("First line\nSecond line\n\n- Third") == ["First line", "Second line", "- Third"]
    assert split_sentences("  \n ... \n") == []


def test_a_long_answer_is_shortened_with_an_offer_of_the_rest():
    s = spoken_answer(ANSWER)
    assert s.truncated and len(s.sentences) == 4 and s.sentences[-1] == (MORE, MORE)
    assert s.sentences[0][0] == "Your total is 1 lakh 39 thousand rupees of the 2 lakh rupees budget (70 percent)."
    assert s.sentences[0][1].startswith("Your total is **INR 1,39,000**")  # what is shown keeps the original
    assert s.text == " ".join(p[0] for p in s.sentences) and "[" not in s.text and "http" not in s.text


def test_the_limits_are_three_sentences_and_about_twenty_seconds():
    five = "One is here. Two is here. Three is here. Four is here. Five is here."
    s = spoken_answer(five)
    assert [p[1] for p in s.sentences[:-1]] == ["One is here.", "Two is here.", "Three is here."] and s.truncated
    words = " ".join(["word"] * 30)
    s = spoken_answer(f"{words}. {words}. Short.", max_sentences=3, max_words=55)
    assert len(s.sentences) == 2 and s.truncated  # 30 + 30 would pass 55 words
    exact = spoken_answer(" ".join(["w"] * 55) + ".", max_words=55)
    assert not exact.truncated
    one_long = spoken_answer(" ".join(["w"] * 80) + ".", max_words=55)
    assert one_long.truncated and len(one_long.sentences[0][0].split()) == 55


def test_a_short_answer_is_not_marked_truncated_and_empty_text_says_nothing():
    s = spoken_answer("The event is in Pune.")
    assert not s.truncated and s.sentences == (("The event is in Pune.", "The event is in Pune."),)
    assert spoken_answer("   ").sentences == () and spoken_answer("").text == ""
    assert spoken_answer("![](http://x.example)").sentences == ()


def test_a_change_is_read_back_so_a_misheard_number_is_caught():
    s = spoken_answer("I have drafted that for you.", readback="Record a payment of INR 5,000 for the venue")
    assert s.sentences[-1][0] == "Please check on screen: Record a payment of 5 thousand rupees for the venue."
    assert verify_spoken("I have drafted that for you.", s.text, "Record a payment of INR 5,000 for the venue")


def test_the_spoken_numbers_are_recomputed_from_the_words():
    assert spoken_values("1 lakh 39 thousand rupees") == {139000.0}
    assert spoken_values("2 crore 50 lakh rupees and 61 thousand 500 rupees and 70 percent") == {25_000_000.0, 61500.0, 70.0}
    assert spoken_values("61,000 left and 1,200 sq ft") == {61000.0, 1200.0}  # a grouped number kept as written is one number
    assert spoken_values("no numbers here") == set() and spoken_values("12 December 2026") == {12.0, 2026.0}
    assert answer_values("INR 1,39,000, 2026-12-12 and 1500.50") == {139000.0, 2026.0, 12.0, 1500.5}


def test_the_rewrite_adds_no_number_and_no_name():
    s = spoken_answer(ANSWER)
    assert verify_spoken(ANSWER, s.text)
    assert not verify_spoken(ANSWER, "Your total is 1 lakh 40 thousand rupees.")  # an invented amount
    assert not verify_spoken(ANSWER, "Ask Meera Iyer about it.")  # an invented name
    assert not verify_spoken("It is 61,000.", "It is 62 thousand rupees.") and verify_spoken("It is 61,000.", "It is 61 thousand rupees.")
    assert verify_spoken("Nothing special.", "Nothing special.")
    assert not verify_spoken("The venue is Orchid Banquet.", "The venue is Skyline Banquet.")
    assert verify_spoken("The venue is Orchid Banquet.", "The venue is Orchid Banquet. Want details? The full answer is on screen.")


def test_every_sentence_of_a_realistic_answer_set_verifies():
    answers = [
        "The caterer is Royal Rasoi at INR 72,000 and the venue is Orchid Banquet at INR 55,000.",
        "You have 120 guests and 61,000 left: that is 30% of the INR 2,00,000 budget.",
        "I cannot find that. Please check with the vendor and tell me what they say.",
    ]
    for a in answers:
        assert verify_spoken(a, spoken_answer(a).text), a


def test_amounts_keep_every_part_and_the_lakh_boundary_is_exact():
    assert speak_numbers("INR 1,39,500") == "1 lakh 39 thousand 500 rupees" and speak_numbers("INR 1,00,000") == "1 lakh rupees" and speak_numbers("INR 25,00,000") == "25 lakh rupees"
    assert speak_numbers("INR 10,00,00,000") == "10 crore rupees" and speak_numbers("INR 1,200") == "1 thousand 200 rupees" and speak_numbers("INR 1,000") == "1 thousand rupees"
    assert speak_numbers("99,999 people") == "99,999 people" and speak_numbers("1,00,000 people") == "1 lakh people" and speak_numbers("1,00,001 people") == "1 lakh 1 people"


def test_a_single_initial_is_not_a_sentence_end():
    assert split_sentences("Ask J. Smith about it. Then call.") == ["Ask J. Smith about it.", "Then call."]
    assert split_sentences("It costs Rs. 650. Done.") == ["It costs Rs. 650.", "Done."]


def test_a_sentence_ending_in_a_lone_capital_letter_is_not_split_it_merges_with_the_next_and_nothing_is_lost():
    assert split_sentences("Choose option A. Then call the venue.") == ["Choose option A. Then call the venue."]  # a documented limit of the initials rule


def test_the_defaults_are_three_sentences_and_fifty_five_words():
    from eventmate.voice import speech

    assert (speech.MAX_SENTENCES, speech.MAX_WORDS) == (3, 55)
    assert not spoken_answer(" ".join(["w"] * 55) + ".").truncated and spoken_answer(" ".join(["w"] * 56) + ".").truncated
    four = spoken_answer("Aa bb. Cc dd. Ee ff. Gg hh.")
    assert len(four.sentences) == 4 and four.truncated and four.sentences[3][0] == MORE  # three sentences then the offer
    assert not spoken_answer("Aa bb. Cc dd. Ee ff.").truncated


def test_a_combined_amount_is_one_number_and_unit_words_need_a_number_before_them():
    assert spoken_values("2 crore 50 lakh") == {25_000_000.0} and spoken_values("3 lakh") == {300_000.0} and spoken_values("1 lakh 39 thousand 500") == {139_500.0}
    assert spoken_values("a lakh of people") == set()


def test_names_are_compared_ignoring_the_first_word_of_each_sentence():
    assert verify_spoken("Rohan is coming. He likes Pune.", "Rohan is coming. He likes Pune.")
    assert not verify_spoken("Rohan is coming.", "Today Rohan and Meera are coming.")
    assert verify_spoken("Ask Rohan.", "Ask Rohan.") and not verify_spoken("ask rohan.", "Please ask Meera.")


def test_table_rows_are_spoken_as_lists_and_the_row_pattern_is_linear_on_blank_lines():
    import time

    from eventmate.voice.speech import _TABLE_ROW, strip_markup

    assert strip_markup("Prices\n| Veg | 450 |\n  | Stage | 1,20,000 |  \nthanks") == "Prices\nVeg, 450\nStage, 1,20,000\nthanks"
    started = time.perf_counter()
    _TABLE_ROW.sub("", "\n" * 40_000 + "x")  # the pattern alone: the markup step that runs before it hides the case, but the pattern must not depend on that
    assert time.perf_counter() - started < 1.0
