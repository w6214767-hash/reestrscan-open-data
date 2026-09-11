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


## Каталог недвижимости Москвы и области

Обновление сначала проверяется тестами. Загрузка использует кэш исходных документов по версии: лимит одного запуска не обрезает каталог навсегда, следующий запуск продолжает недостающие документы. Окно — 7 файлов выгрузки, до 1 200 новых документов за запуск, 8 работников с 15-секундным лимитом детали. Статус указывает `cached_documents`, `pending_documents` и `partial`. Это не заявление о полноте всех торгов региона.

`normalization_revision=1` требует совместимого API РеестрСкан из PR «Муниципальные торги недвижимостью Москвы и Подмосковья». Сначала выпускается API, затем этот импортёр. Источники времени не подменяются текущей датой. Отмена публикуется отдельным событием, применимым к ранее сохранённым лотам.

Структурированная категория важнее слов в описании: машино-места — недвижимость, права водопользования — иные права. Вид аренды не определяется из общего названия «Аренда и продажа»: неизвестный период платы отмечается `rent_unspecified`. Документы связываются с официальными вложениями по ID. Координаты не вычисляются из кадастрового номера или адреса.

Each refresh bounds detail downloading to 10 minutes. Responses are checkpointed individually in the source cache, so interrupted batches retain completed documents and later runs continue pending work. The published feed still changes only after candidate validation.
