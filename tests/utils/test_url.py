from opensprite.utils.url import join_url_path


def test_join_url_path_avoids_duplicate_version_prefix():
    assert join_url_path("https://api.example.com/v1", "/v1/messages") == "https://api.example.com/v1/messages"


def test_join_url_path_avoids_duplicate_full_endpoint():
    assert join_url_path("https://api.example.com/v1/messages", "/v1/messages") == "https://api.example.com/v1/messages"


def test_join_url_path_appends_non_overlapping_endpoint():
    assert join_url_path("https://api.example.com/api/v3", "/browsers") == "https://api.example.com/api/v3/browsers"
