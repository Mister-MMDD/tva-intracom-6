from datetime import date
from decimal import Decimal

# Simulation des données de rates.py
EU_COUNTRIES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE", "MC", "XI",
}

STANDARD_VAT_RATES = {
    "AT": Decimal("20"), "BE": Decimal("21"), "BG": Decimal("20"), "HR": Decimal("25"),
    "CY": Decimal("19"), "CZ": Decimal("21"), "DK": Decimal("25"), "EE": Decimal("24"),   
    "FI": Decimal("25.5"), "FR": Decimal("20"), "DE": Decimal("19"), "GR": Decimal("24"),
    "HU": Decimal("27"), "IE": Decimal("23"), "IT": Decimal("22"), "LV": Decimal("21"),
    "LT": Decimal("21"), "LU": Decimal("17"), "MT": Decimal("18"), "NL": Decimal("21"),
    "PL": Decimal("23"), "PT": Decimal("23"), "RO": Decimal("21"), "SK": Decimal("23"),   
    "SI": Decimal("22"), "ES": Decimal("21"), "SE": Decimal("25"),
    "MC": Decimal("20"), "XI": Decimal("20"),
}

VAT_RATE_HISTORY = [
    ("AT", date(2000, 1, 1), None, Decimal("20.0")),
    ("BE", date(2000, 1, 1), None, Decimal("21.0")),
    ("BG", date(2000, 1, 1), None, Decimal("20.0")),
    ("CY", date(2000, 1, 1), date(2000, 6, 30), Decimal("8.0")),
    ("CY", date(2000, 7, 1), date(2002, 6, 30), Decimal("10.0")),
    ("CY", date(2002, 7, 1), date(2002, 12, 31), Decimal("13.0")),
    ("CY", date(2003, 1, 1), date(2012, 2, 29), Decimal("15.0")),
    ("CY", date(2012, 3, 1), date(2013, 1, 13), Decimal("17.0")),
    ("CY", date(2013, 1, 14), date(2014, 1, 12), Decimal("18.0")),
    ("CY", date(2014, 1, 13), None, Decimal("19.0")),
    ("CZ", date(2000, 1, 1), date(2004, 4, 30), Decimal("22.0")),
    ("CZ", date(2004, 5, 1), date(2009, 12, 31), Decimal("19.0")),
    ("CZ", date(2010, 1, 1), date(2012, 12, 31), Decimal("20.0")),
    ("CZ", date(2013, 1, 1), None, Decimal("21.0")),
    ("DE", date(2000, 1, 1), date(2006, 12, 31), Decimal("16.0")),
    ("DE", date(2007, 1, 1), date(2020, 6, 30), Decimal("19.0")),
    ("DE", date(2020, 7, 1), date(2020, 12, 31), Decimal("16.0")),
    ("DE", date(2021, 1, 1), None, Decimal("19.0")),
    ("DK", date(2000, 1, 1), None, Decimal("25.0")),
    ("EE", date(2000, 1, 1), date(2009, 6, 30), Decimal("18.0")),
    ("EE", date(2009, 7, 1), date(2023, 12, 31), Decimal("20.0")),
    ("EE", date(2024, 1, 1), date(2025, 6, 30), Decimal("22.0")),
    ("EE", date(2025, 7, 1), None, Decimal("24.0")),
    ("ES", date(2000, 1, 1), date(2010, 6, 30), Decimal("16.0")),
    ("ES", date(2010, 7, 1), date(2012, 8, 31), Decimal("18.0")),
    ("ES", date(2012, 9, 1), None, Decimal("21.0")),
    ("FI", date(2000, 1, 1), date(2010, 6, 30), Decimal("22.0")),
    ("FI", date(2010, 7, 1), date(2012, 12, 31), Decimal("23.0")),
    ("FI", date(2013, 1, 1), date(2024, 8, 31), Decimal("24.0")),
    ("FI", date(2024, 9, 1), None, Decimal("25.5")),
    ("FR", date(2000, 1, 1), date(2000, 3, 31), Decimal("20.6")),
    ("FR", date(2000, 4, 1), date(2013, 12, 31), Decimal("19.6")),
    ("FR", date(2014, 1, 1), None, Decimal("20.0")),
    ("GR", date(2000, 1, 1), date(2005, 3, 31), Decimal("18.0")),
    ("GR", date(2005, 4, 1), date(2010, 3, 14), Decimal("19.0")),
    ("GR", date(2010, 3, 15), date(2010, 6, 30), Decimal("21.0")),
    ("GR", date(2010, 7, 1), date(2016, 5, 31), Decimal("23.0")),
    ("GR", date(2016, 6, 1), None, Decimal("24.0")),
    ("HR", date(2000, 1, 1), date(2009, 7, 31), Decimal("22.0")),
    ("HR", date(2009, 8, 1), date(2012, 2, 29), Decimal("23.0")),
    ("HR", date(2012, 3, 1), None, Decimal("25.0")),
    ("HU", date(2000, 1, 1), date(2005, 12, 31), Decimal("25.0")),
    ("HU", date(2006, 1, 1), date(2009, 6, 30), Decimal("20.0")),
    ("HU", date(2009, 7, 1), date(2011, 12, 31), Decimal("25.0")),
    ("HU", date(2012, 1, 1), None, Decimal("27.0")),
    ("IE", date(2000, 1, 1), date(2000, 12, 31), Decimal("21.0")),
    ("IE", date(2001, 1, 1), date(2002, 2, 28), Decimal("20.0")),
    ("IE", date(2002, 3, 1), date(2008, 11, 30), Decimal("21.0")),
    ("IE", date(2008, 12, 1), date(2009, 12, 31), Decimal("21.5")),
    ("IE", date(2010, 1, 1), date(2011, 12, 31), Decimal("21.0")),
    ("IE", date(2012, 1, 1), date(2020, 8, 31), Decimal("23.0")),
    ("IE", date(2020, 9, 1), date(2021, 2, 28), Decimal("21.0")),
    ("IE", date(2021, 3, 1), None, Decimal("23.0")),
    ("IT", date(2000, 1, 1), date(2011, 9, 16), Decimal("20.0")),
    ("IT", date(2011, 9, 17), date(2013, 9, 30), Decimal("21.0")),
    ("IT", date(2013, 10, 1), None, Decimal("22.0")),
    ("LT", date(2000, 1, 1), date(2008, 12, 31), Decimal("18.0")),
    ("LT", date(2009, 1, 1), date(2009, 8, 31), Decimal("19.0")),
    ("LT", date(2009, 9, 1), None, Decimal("21.0")),
    ("LU", date(2000, 1, 1), date(2014, 12, 31), Decimal("15.0")),
    ("LU", date(2015, 1, 1), date(2022, 12, 31), Decimal("17.0")),
    ("LU", date(2023, 1, 1), date(2023, 12, 31), Decimal("16.0")),
    ("LU", date(2024, 1, 1), None, Decimal("17.0")),
    ("LV", date(2000, 1, 1), date(2008, 12, 31), Decimal("18.0")),
    ("LV", date(2009, 1, 1), date(2010, 12, 31), Decimal("21.0")),
    ("LV", date(2011, 1, 1), date(2012, 6, 30), Decimal("22.0")),
    ("LV", date(2012, 7, 1), None, Decimal("21.0")),
    ("MC", date(2000, 1, 1), date(2000, 3, 31), Decimal("20.6")),
    ("MC", date(2000, 4, 1), date(2013, 12, 31), Decimal("19.6")),
    ("MC", date(2014, 1, 1), None, Decimal("20.0")),
    ("MT", date(2000, 1, 1), date(2003, 12, 31), Decimal("15.0")),
    ("MT", date(2004, 1, 1), None, Decimal("18.0")),
    ("NL", date(2000, 1, 1), date(2000, 12, 31), Decimal("17.5")),
    ("NL", date(2001, 1, 1), date(2012, 9, 30), Decimal("19.0")),
    ("NL", date(2012, 10, 1), None, Decimal("21.0")),
    ("PL", date(2000, 1, 1), date(2010, 12, 31), Decimal("22.0")),
    ("PL", date(2011, 1, 1), None, Decimal("23.0")),
    ("PT", date(2000, 1, 1), date(2002, 6, 4), Decimal("17.0")),
    ("PT", date(2002, 6, 5), date(2005, 6, 30), Decimal("19.0")),
    ("PT", date(2005, 7, 1), date(2008, 6, 30), Decimal("21.0")),
    ("PT", date(2008, 7, 1), date(2010, 6, 30), Decimal("20.0")),
    ("PT", date(2010, 7, 1), date(2010, 12, 31), Decimal("21.0")),
    ("PT", date(2011, 1, 1), None, Decimal("23.0")),
    ("RO", date(2000, 1, 1), date(2010, 6, 30), Decimal("19.0")),
    ("RO", date(2010, 7, 1), date(2015, 12, 31), Decimal("24.0")),
    ("RO", date(2016, 1, 1), date(2016, 12, 31), Decimal("20.0")),
    ("RO", date(2017, 1, 1), date(2025, 7, 31), Decimal("19.0")),
    ("RO", date(2025, 8, 1), None, Decimal("21.0")),
    ("SE", date(2000, 1, 1), None, Decimal("25.0")),
    ("SI", date(2000, 1, 1), date(2001, 12, 31), Decimal("19.0")),
    ("SI", date(2002, 1, 1), date(2013, 6, 30), Decimal("20.0")),
    ("SI", date(2013, 7, 1), None, Decimal("22.0")),
    ("SK", date(2000, 1, 1), date(2002, 12, 31), Decimal("23.0")),
    ("SK", date(2003, 1, 1), date(2003, 12, 31), Decimal("20.0")),
    ("SK", date(2004, 1, 1), date(2010, 12, 31), Decimal("19.0")),
    ("SK", date(2011, 1, 1), date(2024, 12, 31), Decimal("20.0")),
    ("SK", date(2025, 1, 1), None, Decimal("23.0")),
]

start_date = date(2000, 1, 1)
end_date = date(2026, 9, 1)

def get_rate(country, d):
    for c, d_from, d_to, rate in VAT_RATE_HISTORY:
        if c == country:
            if d_from <= d:
                if d_to is None or d <= d_to:
                    return rate
    return STANDARD_VAT_RATES.get(country)

with open("seed_vat_rates.sql", "w", encoding="utf-8") as f:
    f.write("-- Clear table\n")
    f.write("DELETE FROM vat_rate_cache;\n\n")
    f.write("-- Populate with historical standard rates since 2000\n")
    f.write("INSERT INTO vat_rate_cache (country_code, rate_type, situation_date, rate, fetched_at)\nVALUES\n")

    rows = []
    curr_date = start_date
    while curr_date <= end_date:
        for country in sorted(EU_COUNTRIES):
            rate = get_rate(country, curr_date)
            if rate is not None:
                rows.append(f"('{country}', 'STANDARD', '{curr_date.isoformat()}', {rate}, NOW())")
        
        if curr_date.month == 12:
            curr_date = date(curr_date.year + 1, 1, 1)
        else:
            curr_date = date(curr_date.year, curr_date.month + 1, 1)

    f.write(",\n".join(rows))
    f.write(";\n")

print(f"Generated seed_vat_rates.sql with {len(rows)} rows.")
