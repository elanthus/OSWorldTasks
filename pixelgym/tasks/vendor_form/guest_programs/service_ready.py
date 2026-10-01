import time,urllib.request
last_error = None
for attempt in range(240):
 try:
  body=urllib.request.urlopen('__PIXELGYM_APP_URL__healthz',timeout=1).read()
  print('READY')
  break
 except Exception as exc:
  last_error = repr(exc)
  if attempt == 239: raise RuntimeError(last_error)
  time.sleep(0.25)
