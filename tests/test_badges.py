import pytest
from app.scraper import normalize_title


def test_normalize_title_basic():
    assert normalize_title("Build a Website on Google Cloud") == "build a website on google cloud"


def test_normalize_title_unicode_nfkc():
    # Full-width characters
    raw = "Ｂｕｉｌｄ　ａ　Ｗｅｂｓｉｔｅ"
    assert normalize_title(raw) == "build a website"


def test_normalize_title_dash_variants():
    # Different dashes: en-dash, em-dash, minus sign, hyphen
    dash1 = "Data on Google Cloud - Console"
    dash2 = "Data on Google Cloud – Console"
    dash3 = "Data on Google Cloud — Console"
    dash4 = "Data on Google Cloud − Console"
    
    assert normalize_title(dash1) == normalize_title(dash2)
    assert normalize_title(dash2) == normalize_title(dash3)
    assert normalize_title(dash3) == normalize_title(dash4)
    assert normalize_title(dash1) == "data on google cloud - console"


def test_normalize_title_whitespace_collapse():
    raw = "  Implement    Speech   and   Language Solutions  \n with Pre-trained APIs\t  "
    expected = "implement speech and language solutions with pre-trained apis"
    assert normalize_title(raw) == expected


def test_normalize_title_trailing_punctuation():
    raw = "The Basics of Google Cloud Compute.!"
    assert normalize_title(raw) == "the basics of google cloud compute"
    raw2 = "Analyze Images with the Cloud Vision API: "
    assert normalize_title(raw2) == "analyze images with the cloud vision api"
