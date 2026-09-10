# ReestrScan Open Data bridge

Public, free bridge from the official [GIS Torgi OpenData](https://torgi.gov.ru/new/opendata/) publication to the normalized ReestrScan auctions feed.

## Published files

- `public/feed-v1.json` — normalized feed compatible with ReestrScan feed contract v1.
- `public/status.json` — last successful bridge run and quality counters.

Raw feed URL:

`https://raw.githubusercontent.com/w6214767-hash/reestrscan-open-data/main/public/feed-v1.json`

## Scope

The pilot processes Moscow (77) and Moscow Oblast (50). It uses only public data from `torgi.gov.ru`, requires no paid API and stores no credentials or customer data.

GitHub Actions checks the code on every change and refreshes the feed on a schedule. A failed or empty upstream download never replaces the last valid feed.

This project is not affiliated with GIS Torgi. Source records remain authoritative; every lot links back to the official notice.
