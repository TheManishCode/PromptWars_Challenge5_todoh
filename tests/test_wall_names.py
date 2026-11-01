"""8.4: names. Server-side validation; the stored value is the normalized one."""

import pytest

from eventmate.wall.names import InvalidName, normalize, validate


@pytest.mark.parametrize("name,stored", [
    ("Asha", "Asha"), ("  Ravi   K. ", "Ravi K."), ("O'Neil", "O'Neil"), ("Zoë", "Zoë"), ("मोहन", "मोहन"), ("李小龍", "李小龍"), ("Ana-Maria", "Ana-Maria"),
    ("007 Bond", "007 Bond"), ("Essex", "Essex"), ("Dickinson", "Dickinson"), ("Shitij", "Shitij"), ("Nazim", "Nazim"), ("Matthew", "Matthew"),
    ("he\u200bllo", "hello"), ("Ａｓｈａ", "Asha"), ("ﬁsh", "fish"),
])
def test_good_names_are_stored_normalized(name, stored):
    assert validate(name) == stored


@pytest.mark.parametrize("bad", [
    "", " ", "a", "x" * 25, "x" * 500, "<b>hi</b>", "<script>alert(1)</script>", "a@b.com", "www.evil.com", "http://x.y", "evil dot com", "name at gmail dot com", "tel:12345",
    "{{7*7}}", "${jndi:ldap://x}", "a/b", "a\\b", "hello😀", "..", "--", "1 2", "007", "aaaaaaaa",
    "Раul", "ɑdmin", "pаypal",  # Cyrillic and IPA look-alikes
    "\u200b\u200b\u200b\u200b\u200b\u200bab",  # many hidden characters
    "fuck", "f u c k", "fuuuuck", "sex", "Sex Dick",
    None, 5, ["Asha"], {"name": "Asha"}, b"Asha",
])
def test_bad_names_are_refused(bad):
    with pytest.raises(InvalidName):
        validate(bad)


def test_empty_after_normalization_is_refused():
    with pytest.raises(InvalidName):
        validate("\u200b\u200d\u2060")


def test_bidi_and_zero_width_are_removed_not_kept():
    out = validate("ab\u202ecd\u200b")
    assert out == "abcd" and "\u202e" not in out and "\u200b" not in out


def test_normalize_collapses_whitespace_and_folds_width():
    assert normalize("A\t\n  B") == "A B"
    assert normalize("Ａ") == "A"


def test_the_message_never_echoes_the_input():
    for bad in ("<img src=x onerror=1>", "a@b.com"):
        with pytest.raises(InvalidName) as e:
            validate(bad)
        assert bad not in str(e.value)


def test_a_custom_blocklist_can_be_supplied_and_an_unreadable_file_blocks_nothing(tmp_path):
    from eventmate.wall import names
    assert names._blocklist(tmp_path / "missing.txt") == ((), ())
    with pytest.raises(InvalidName):
        validate("Banana", blocklist=(("banana",), ()))
    f = tmp_path / "b.txt"
    f.write_text("# c\n\n~frag\nword\n")
    assert names._blocklist(f) == (("word",), ("frag",))


@pytest.mark.parametrize("name", ["Visitor 1515 x", "1515 Maple", "Room 5ex"[:0] + "Ana 007", "Gate 1 3 4"])
def test_a_number_is_not_read_as_look_alike_letters(name):
    assert validate(name) == name  # "1515" would fold to "isis" if digits were always read as letters


@pytest.mark.parametrize("bad", ["sh1t", "S3x", "s3x dick"])
def test_look_alike_digits_inside_a_word_are_still_read_as_letters(bad):
    with pytest.raises(InvalidName):
        validate(bad)
