# ReestrScan Open Data bridge

Public, free bridge from the official [GIS Torgi OpenData](https://torgi.gov.ru/new/opendata/) publication to the normalized ReestrScan auctions feed.

## Published files

- `public/feed-v1.json` — normalized feed compatible with ReestrScan feed contract v1.
- `public/status.json` — last successful bridge run and quality counters.

Raw feed URL:

`https://raw.githubusercontent.com/w6214767-hash/reestrscan-open-data/main/public/feed-v1.json`

## Scope

The pilot processes Moscow (77) and Moscow Oblast (50). It uses only public data from `torgi.gov.ru`, requires no paid API and stores no credentials or customer data.

The upstream portal warns that access from IP addresses outside Russia, including VPNs, can be unavailable. For that reason:

- tests run on a standard GitHub-hosted runner;
- scheduled data fetching runs on a free self-hosted macOS ARM64 runner with the custom label `gis-torgi-ru` and a Russian network route;
- the self-hosted runner has read-only repository permissions and receives no persistent GitHub credential;
- it uploads a one-day candidate artifact, which a separate GitHub-hosted job validates and publishes;
- refresh runs every six hours while the self-hosted runner is online;
- a failed or empty upstream download never replaces the last valid feed.

The fixed-target diagnostic is available as `python3 scripts/probe_transport.py`. It never prints response bodies.

This project is not affiliated with GIS Torgi. Source records remain authoritative; every lot links back to the official notice.
