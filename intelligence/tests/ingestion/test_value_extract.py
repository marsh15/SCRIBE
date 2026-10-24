from __future__ import annotations

from scribe_ingest.value_extract import extract_values, pair_value_changes


def test_extracts_units_percentages_currency_dates_and_normative_sentences():
    text = (
        "On 1 January 2024, the cap was EUR 45.00. It changes on 2026-06-30 to €48.50. "
        "Employees may carry over 5 unused working days and receive 80 percent of base pay. "
        "Requests must be submitted by May 3, 2026."
    )

    values = extract_values(text)
    observed = {(value.kind, value.value) for value in values}

    assert ("date", "1 January 2024") in observed
    assert ("date", "2026-06-30") in observed
    assert ("date", "May 3, 2026") in observed
    assert ("currency", "EUR 45.00") in observed
    assert ("currency", "€48.50") in observed
    assert ("number", "5 unused working days") in observed
    assert ("percentage", "80 percent") in observed
    assert any(kind == "normative" and "must be submitted" in value for kind, value in observed)


def test_extracts_suffix_currency_compound_and_degree_units_with_exact_offsets():
    text = "A fee of 45 € applies. Speed is 8 km/h; temperature is 2 °C and 5°F."

    values = extract_values(text)
    observed = {(value.kind, value.value) for value in values}

    assert ("currency", "45 €") in observed
    assert ("number", "8 km/h") in observed
    assert ("number", "2 °C") in observed
    assert ("number", "5°F") in observed
    assert all(text[value.start:value.end] == value.value for value in values)


def test_pairs_suffix_currency_compound_and_degree_unit_changes():
    old = "A fee of 45 € applies. Speed is 8 km/h; temperature is 2 °C."
    new = "A fee of 50 € applies. Speed is 10 km/h; temperature is 4 °C."

    changes = pair_value_changes(old, new)
    pairs = {(change.kind, change.old_value, change.new_value) for change in changes}

    assert ("currency", "45 €", "50 €") in pairs
    assert ("number", "8 km/h", "10 km/h") in pairs
    assert ("number", "2 °C", "4 °C") in pairs
    assert all(
        old[change.old_start:change.old_end] == change.old_value
        and new[change.new_start:change.new_end] == change.new_value
        for change in changes
        if change.old_value and change.new_value
    )


def test_new_patterns_do_not_accept_partial_tokens():
    text = "45 €foo, 8 km/hour, 2 °Celsius, and 30days."

    assert extract_values(text) == []


def test_pairs_changed_values_by_shared_sentence_context():
    old = (
        "Full-time employees receive 24 working days. Up to 5 unused days may be carried over "
        "and must be taken by 31 March of the following year. A medical certificate is required "
        "after 3 consecutive working days. Parental leave is 12 weeks at 80% of base pay. "
        "The hotel cap is EUR 160 per night."
    )
    new = (
        "Full-time employees receive 27 working days. Up to 8 unused days may be carried over "
        "and must be taken by 30 June of the following year. A medical certificate is required "
        "after 5 consecutive working days. Parental leave is 16 weeks at 100% of base pay. "
        "The hotel cap is EUR 175 per night."
    )

    changes = pair_value_changes(old, new)
    pairs = {(change.kind, change.old_value, change.new_value) for change in changes}

    assert ("number", "24 working days", "27 working days") in pairs
    assert ("number", "5 unused days", "8 unused days") in pairs
    assert ("date", "31 March", "30 June") in pairs
    assert ("number", "3 consecutive working days", "5 consecutive working days") in pairs
    assert ("number", "12 weeks", "16 weeks") in pairs
    assert ("percentage", "80%", "100%") in pairs
    assert ("currency", "EUR 160", "EUR 175") in pairs
    assert any(kind == "normative" and "31 March" in old_value and "30 June" in new_value
               for kind, old_value, new_value in pairs if kind == "normative")


def test_added_and_removed_values_are_retained_and_unchanged_values_omitted():
    changes = pair_value_changes("Policy grants 10 working days.", "Policy grants 10 working days and 2 extra weeks.")

    assert [(change.kind, change.old_value, change.new_value) for change in changes] == [
        ("number", None, "2 extra weeks"),
    ]


def test_unrelated_values_are_not_paired_by_position_alone():
    changes = pair_value_changes("The old rate is 5 working days.", "The new deadline is 30 June.")

    number_changes = [change for change in changes if change.kind == "number"]
    date_changes = [change for change in changes if change.kind == "date"]
    assert [(change.old_value, change.new_value) for change in number_changes] == [("5 working days", None)]
    assert [(change.old_value, change.new_value) for change in date_changes] == [(None, "30 June")]
