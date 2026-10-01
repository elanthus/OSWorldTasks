import json,time,urllib.request
last_error = None
ready_streak = 0
for attempt in range(240):
 try:
  value=json.load(urllib.request.urlopen('__PIXELGYM_APP_URL__api/page-ready',timeout=1))
  if value == {'ready': True}:
   ready_streak += 1
   if ready_streak == 2:
    print(json.dumps(value,sort_keys=True,separators=(',',':')))
    break
  else:
   ready_streak = 0
   last_error = 'page-ready response: ' + repr(value)
 except Exception as exc:
  ready_streak = 0
  last_error = repr(exc)
 if attempt == 239: raise RuntimeError(last_error or 'page did not report ready')
 time.sleep(0.25)
