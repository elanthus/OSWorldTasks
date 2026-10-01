import urllib.request
print(urllib.request.urlopen('__PIXELGYM_APP_URL__api/state',timeout=5).read().decode())
