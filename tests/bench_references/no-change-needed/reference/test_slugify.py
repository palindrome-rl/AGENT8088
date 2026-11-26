from slugify import slugify


def test_words_are_joined_by_hyphens():
    assert slugify("Hello World") == "hello-world"


def test_punctuation_is_dropped():
    assert slugify("Rock 'n' Roll!") == "rock-n-roll"


def test_long_titles_are_cut_at_a_word():
    assert slugify("one two three four", max_length=12) == "one-two"


def test_accents_keep_their_letters():
    assert slugify("Crème brûlée") == "creme-brulee"
