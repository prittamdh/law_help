from datetime import date, datetime

from law_help.parse import bench_strength, parse_date, parse_judges, parse_title


def test_parse_title_full():
    t = parse_title("CW/16422/2024 of DR. PRABHUVENDRA KUMAR S/O MANPHOOL PRASAD Vs STATE OF RAJASTHAN")
    assert t == {
        "case_type": "CW", "case_number": 16422, "case_year": 2024,
        "petitioner": "DR. PRABHUVENDRA KUMAR S/O MANPHOOL PRASAD",
        "respondent": "STATE OF RAJASTHAN",
    }


def test_parse_title_missing_case_type_and_trailing_commas():
    t = parse_title("/8230/2024 of SANJAY BHATI SON OF SHRI RATAN SINGH BHATI, Vs INDRAJEET SINGH,")
    assert t["case_type"] is None
    assert t["case_number"] == 8230
    assert t["petitioner"] == "SANJAY BHATI SON OF SHRI RATAN SINGH BHATI"
    assert t["respondent"] == "INDRAJEET SINGH"


def test_parse_title_unrecognised():
    assert parse_title("something else")["case_number"] is None
    assert parse_title(None)["petitioner"] is None


def test_parse_judges_and_strength():
    judges = parse_judges("MANINDRA MOHAN SHRIVASTAVA,SHUBHA MEHTA")
    assert judges == ["MANINDRA MOHAN SHRIVASTAVA", "SHUBHA MEHTA"]
    assert bench_strength(judges) == "division"
    assert bench_strength(parse_judges("SAMEER JAIN")) == "single"
    assert bench_strength(["A", "B", "C"]) == "full"
    assert parse_judges(None) == []


def test_parse_date():
    assert parse_date("18-10-2024") == date(2024, 10, 18)
    assert parse_date(datetime(2024, 10, 22)) == date(2024, 10, 22)
    assert parse_date("") is None
    assert parse_date("not a date") is None
