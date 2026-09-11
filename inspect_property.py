import json, bridge
meta = bridge.fetch_json(bridge.META_URL, max_bytes=4*1024*1024)
sources = bridge.discover_sources(meta, 1)
rows, _ = bridge.select_rows([bridge.fetch_json(sources[0])], 12)
for row in rows:
 try:
  raw = bridge.fetch_json(bridge.urljoin(bridge.META_URL, row['href']), max_bytes=16*1024*1024)
  notice = bridge._notice(raw)
  print('ROW', json.dumps(row, ensure_ascii=False))
  if not notice:
   print('WRAPPER', json.dumps(raw, ensure_ascii=False)[:1800]); continue
  print('COMMON', json.dumps(notice.get('commonInfo'), ensure_ascii=False))
  for lot in notice.get('lots', [])[:1]:
   info = lot.get('biddingObjectInfo', {})
   print('LOT_KEYS', list(lot))
   print('INFO', json.dumps(info, ensure_ascii=False)[:22000])
   for key in ('documents','attachments','biddConditions','priceMin','lotName','lotDescription'):
    if key in lot: print(key, json.dumps(lot[key], ensure_ascii=False)[:3000])
 except bridge.BridgeError as e: print('ERROR', str(e))
