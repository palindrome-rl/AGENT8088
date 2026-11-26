from slugify import slugify


def test_accents_keep_their_letters():
    assert slugify("Crème brûlée") == "creme-brulee"
