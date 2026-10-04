import csv
from collections import Counter
from decimal import Decimal

reader = csv.DictReader(open('data/ventes_multian_test_new2.csv', encoding='utf-8'))
rows = list(reader)

print(f'Total: {len(rows)}')

# Ventes en devises
fx_rows = [r for r in rows if r.get('TRANSACTION_CURRENCY_CODE') and r['TRANSACTION_CURRENCY_CODE'] != 'EUR']
print(f'Ventes en devises: {len(fx_rows)}')
print('Devises:', Counter(r.get('TRANSACTION_CURRENCY_CODE', 'EUR') for r in rows))
print()

# Combiner devises avec pays
fx_country_currency = Counter(f"{r['ARRIVAL_COUNTRY']}->{r['TRANSACTION_CURRENCY_CODE']}" for r in fx_rows)
print('Combinaisons pays->devise:')
for combo, count in fx_country_currency.most_common():
    print(f"  {combo}: {count}")
print()

# VIES
vies_rows = [r for r in rows if r.get('BUYER_VAT_NUMBER')]
print(f'Avec VAT: {len(vies_rows)}')

# Compter par pays en utilisant BUYER_VAT_NUMBER_COUNTRY quand disponible
vies_countries = Counter()
for r in vies_rows:
    country = r.get('BUYER_VAT_NUMBER_COUNTRY', '')
    if country:
        vies_countries[country] += 1
    else:
        # Fallback: utiliser les 2 premiers caractères du numéro
        vat = r.get('BUYER_VAT_NUMBER', '')
        if len(vat) >= 2:
            vies_countries[vat[:2]] += 1
print('Pays des VAT:', vies_countries)

# VIES valides (de la liste fournie) - détectés par la note
real_vies = [r for r in vies_rows if 'VALIDE' in r.get('ITEM_DESCRIPTION', '')]
invalid_vies = [r for r in vies_rows if 'INVALIDE' in r.get('ITEM_DESCRIPTION', '')]
genere_vies = [r for r in vies_rows if 'GENERE' in r.get('ITEM_DESCRIPTION', '')]
nif_rows = [r for r in vies_rows if 'NIF' in r.get('ITEM_DESCRIPTION', '')]
dom_rc_rows = [r for r in vies_rows if 'autoliquidation domestique' in r.get('ITEM_DESCRIPTION', '')]

print(f'VIES valides (réels): {len(real_vies)}')
print(f'VIES invalides: {len(invalid_vies)}')
print(f'VIES générés: {len(genere_vies)}')
print(f'NIF nationaux: {len(nif_rows)}')
print(f'B2B autoliquidation domestique: {len(dom_rc_rows)}')

# OSS rows
eu_countries = ["DE", "IT", "ES", "NL", "PL", "BE", "AT", "PT", "SE", "DK", "FI", "CZ", "HU", "RO", "GR", "SK", "HR", "LT", "LV", "BG"]
oss_rows = [r for r in rows if r.get('TRANSACTION_TYPE') == 'SHIPMENT' and not r.get('BUYER_VAT_NUMBER') and r.get('ARRIVAL_COUNTRY') in eu_countries and r.get('DEPARTURE_COUNTRY') in eu_countries and r.get('PRODUCT_TAX_CODE') != 'A_GEN_NOTAX' and r.get('TRANSACTION_CURRENCY_CODE') == 'EUR']
print(f'Ventes OSS potentielles: {len(oss_rows)}')
if oss_rows:
    oss_total = sum(Decimal(r['PRICE_OF_ITEMS_AMT_VAT_EXCL']) for r in oss_rows)
    print(f'Total OSS: {oss_total} EUR')

