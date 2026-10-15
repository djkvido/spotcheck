from spotcheck import settings


def test_merge_clamps_values_into_bounds():
    result = settings.merge(settings.defaults(), {"accept_ber": 0.9, "threads": 99, "keep_backups": 0})
    assert result["accept_ber"] == 0.48
    assert result["threads"] == 8
    assert result["keep_backups"] == 1


def test_merge_keeps_thresholds_consistent():
    result = settings.merge(settings.defaults(), {"accept_ber": 0.35, "unsure_ber": 0.2, "strong_ber": 0.4})
    assert result["unsure_ber"] >= result["accept_ber"]
    assert result["strong_ber"] <= result["accept_ber"]


def test_merge_repairs_settings_that_would_break_processing():
    result = settings.merge(
        settings.defaults(), {"audio_providers": ["nonsense"], "queries": [], "use_deezer": False, "use_itunes": False}
    )
    assert result["audio_providers"] == settings.DEFAULTS["audio_providers"]
    assert result["queries"] == settings.DEFAULTS["queries"]
    assert result["use_deezer"] is True


def test_merge_ignores_unknown_keys_and_bad_values():
    result = settings.merge(settings.defaults(), {"unknown": 1, "threads": "many"})
    assert "unknown" not in result
    assert result["threads"] == settings.DEFAULTS["threads"]


def test_merge_accepts_multiline_text_for_lists():
    result = settings.merge(settings.defaults(), {"queries": "{artist} {title}\n\n{title}\n"})
    assert result["queries"] == ["{artist} {title}", "{title}"]


def test_defaults_are_independent_copies():
    first = settings.defaults()
    first["queries"].append("x")
    assert "x" not in settings.defaults()["queries"]


def test_merge_parses_boolean_strings():
    assert settings.merge(settings.defaults(), {"auto_replace": "false"})["auto_replace"] is False
    assert settings.merge(settings.defaults(), {"auto_replace": "true"})["auto_replace"] is True


def test_merge_drops_obsolete_keys_from_stored_settings():
    assert "removed_option" not in settings.merge({"removed_option": 1, "threads": 2}, {})


def test_candidate_sources_are_validated():
    result = settings.merge(settings.defaults(), {"candidate_sources": ["soundcloud", "soundcloud", "x"]})
    assert result["candidate_sources"] == ["soundcloud"]
    assert settings.merge(settings.defaults(), {"candidate_sources": []})["candidate_sources"] == list(
        settings.DEFAULTS["candidate_sources"]
    )


def test_validate_explains_each_problem():
    current = settings.defaults()
    errors = settings.validate(
        {
            "accept_ber": 0.35,
            "unsure_ber": 0.3,
            "bad_source_words": "live|(",
            "queries": ["{artist} {titel}"],
            "use_deezer": False,
            "use_itunes": False,
        },
        current,
    )
    assert errors["unsure_ber"]["code"] == "unsure_below_accept"
    assert errors["bad_source_words"]["code"] == "invalid_regex"
    assert errors["queries"] == {
        "code": "query_unknown_field",
        "params": {"query": "{artist} {titel}", "field": "titel"},
    }
    assert errors["use_deezer"]["code"] == "preview_source_required"
    assert settings.validate({"threads": "many"}, current)["threads"]["code"] == "not_a_number"
    assert settings.validate({"queries": ["{title} {hint}"]}, current)["queries"]["code"] == "query_needs_plain"
    assert settings.validate({"accept_ber": 0.25, "threads": 4}, current) == {}
