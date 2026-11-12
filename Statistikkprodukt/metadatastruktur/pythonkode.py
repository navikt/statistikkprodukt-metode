import yaml
import pandas as pd

FIELD_MAPPING = {
    "id": ("matrix", "PX"),
    "subject": ("subject", "PX"),
    "subject-code": ("subject-code", "PX"),
    "subject-area": ("subject-area", "PX"),
    "navn": ("title", "PX"),
    "innhold": ("contents", "PX"),
    "note": ("note", "PX"),
    "kontakt": ("contact", "PX"),
}


def add_row(rows, row_type, keyword, language, value):
    if value is not None:
        rows.append(
            {
                "TYPE": row_type,
                "KEYWORD": keyword,
                "language": language,
                "VALUE": value,
            }
        )


def add_dimension_rows(rows, item):
    column = item["kolonne"]

    # Name
    add_row(rows, "CR", column, "no", item.get("navn"))
    add_row(rows, "CR", column, "en", item.get("navn-en"))

    # Unit
    if "unit" in item:
        add_row(rows, "CR", f"unit_{column}", "no", item["unit"])

    if "unit-en" in item:
        add_row(rows, "CR", f"unit_{column}", "en", item["unit-en"])

    # SQ
    if "sq" in item:
        add_row(rows, "SQ", column, "", item["sq"])


def metadata_rows_from_yaml(filepath: str) -> list:
    with open(filepath, encoding="utf-8") as f:
        yaml_data = yaml.safe_load(f)

    d = yaml_data["definisjon"]

    rows = []

    #
    # PX metadata
    #
    for yaml_field, (keyword, field_type) in FIELD_MAPPING.items():

        if yaml_field in d:
            add_row(
                rows,
                field_type,
                keyword,
                "no",
                d[yaml_field],
            )

        english_field = f"{yaml_field}-en"

        if english_field in d:
            add_row(
                rows,
                field_type,
                keyword,
                "en",
                d[english_field],
            )

    #
    # Periodisering
    #
    if "periodisering" in d:

        p = d["periodisering"]

        add_row(rows, "CR", p["kolonne"], "no", p["navn"])
        add_row(rows, "CR", p["kolonne"], "en", p.get("navn-en"))

        if "sq" in p:
            add_row(rows, "SQ", p["kolonne"], "", p["sq"])

        if "timeval" in p:
            add_row(rows, "CS", "TIMEVAL", "", p["timeval"])

    #
    # Variabler
    #
    for variabel in d.get("variabler", []):
        add_dimension_rows(rows, variabel)

    #
    # Tellepunkter
    #
    for tellepunkt in d.get("tellepunkter", []):
        add_dimension_rows(rows, tellepunkt)

    #
    # STUB
    #
    stub_columns = [
        v["kolonne"]
        for v in d.get("variabler", [])
    ]

    if "periodisering" in d:
        stub_columns.append(
            d["periodisering"]["kolonne"]
        )

    add_row(
        rows,
        "CS",
        "STUB",
        "",
        ",".join(stub_columns),
    )

    #
    # DATA
    #
    data_columns = []

    for tp in d.get("tellepunkter", []):

        value = tp["kolonne"]

        if "unit" in tp:
            value += f"#1#unit_{tp['kolonne']}"

        data_columns.append(value)

    add_row(
        rows,
        "CS",
        "DATA",
        "",
        ",".join(data_columns),
    )

    return rows


if __name__ == "__main__":

    rows = metadata_rows_from_yaml("meta.yml")

    df = pd.DataFrame(rows)

    print("\nMetadata table:\n")
    print(df.to_string(index=False))

    # Export if wanted
    # df.to_excel("metadata.xlsx", index=False)