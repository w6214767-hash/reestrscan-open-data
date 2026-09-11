import json, bridge
href='https://torgi.gov.ru/new/opendata/7710568760-notice/docs/notice_21000004710000027997_695b99bc-4df0-4d0f-b544-0ca454e561e4.json'
raw=bridge.fetch_json(href, max_bytes=16*1024*1024)
def clean(value):
 if isinstance(value, dict): return {k:clean(v) for k,v in value.items() if k not in ('signedData','detachedSignature')}
 if isinstance(value, list): return [clean(x) for x in value]
 return value
print(json.dumps(clean(raw), ensure_ascii=False))
