"""Reproduce the reviewed five-card export offline, without overwriting it."""
import csv
import json
import re
from pathlib import Path

RUN = Path(__file__).resolve().parent
CITIES = ['Люберцы', 'Москва', 'Коммунарка', 'Москва', 'Подольск']


def main():
    queue = json.loads((RUN / 'evidence/queue.json').read_text())
    assert len(queue) == len({x['id'] for x in queue}) == 5
    rows = []
    for rank, (item, city) in enumerate(zip(queue, CITIES), 1):
        identity = item['id']
        raw = json.loads((RUN / f'raw/{identity}.raw.json').read_text())
        assert raw['state'] == 'DETAIL' and raw['id'] == identity
        assert raw['description'] and raw['params_blocks']
        snap = (RUN / f'evidence/{identity}.txt').read_text()
        block = snap.split('- heading "Расположение" [level=2]\n', 1)[1].split('- heading "Описание"', 1)[0]
        location = ' '.join(json.loads(line[8:]) for line in block.splitlines() if line.startswith('- text: "'))
        assert location
        def number(value):
            match = re.search(r'\d+', (value or '').replace('\xa0', '').replace(' ', ''))
            return match.group() if match else ''
        row = dict(id=identity, city=city, profession='',
                   segments='all_professions_all_regions_vahta_first5',
                   title=raw['title'], salary_raw=raw['salary_raw'] or '',
                   employer_name_observed='; '.join(dict.fromkeys(raw['employer_raw'])),
                   location_observed=location, source_url=raw['source_url'],
                   observed_at=raw['observed_at'], description=raw['description'],
                   employer_profile_url='', date_raw=raw['date_raw'] or '',
                   views_total=number(raw['views_raw']), views_today=number(raw['today_views_raw']),
                   full_parameters_read='True', analysis_status='validated', export_eligibility='included')
        for field in raw['fields']:
            name = ' '.join(field['name'].split())
            assert name not in row
            row[name] = field['value']
        assert row['График'] == 'Вахта'
        row.update(read_status_parameters='observed', read_status_description='observed',
                   read_status_employer='observed', read_status_location='observed',
                   read_limitations='rendered_DOM_only; employer_profile_not_opened; stitched_screenshot_repeats_viewport; live_route_not_verified',
                   listing_rank=rank, listing_promoted=str(item['promoted']),
                   enrichment_status='not_run', enriched_fields='', enrichment_version='')
        review = dict(row=row, review=dict(status='validated',
                      method='All rendered Conditions and Requirements reviewed against raw, snapshot and screenshot',
                      location_source=f'evidence/{identity}.txt#Расположение',
                      raw=f'raw/{identity}.raw.json', generic_values_tokens_used=False,
                      description_enrichment='not_run'))
        with (RUN / f'cards/{identity}.json').open('x') as stream:
            json.dump(review, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        rows.append(row)
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with (RUN / 'exports/vacancies_flat.csv').open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    with (RUN / 'exports/vacancies.json').open('x') as stream:
        json.dump(rows, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(f'{len(rows)} reviewed cards; {len(columns)} columns')


if __name__ == '__main__':
    main()
