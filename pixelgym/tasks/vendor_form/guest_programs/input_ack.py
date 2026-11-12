import urllib.request
print(urllib.request.urlopen('__PIXELGYM_APP_URL__api/input-ack',timeout=5).read().decode())
