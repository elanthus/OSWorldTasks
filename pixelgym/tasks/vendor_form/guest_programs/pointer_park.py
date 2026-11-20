import json,os
os.environ.setdefault('DISPLAY',':0')
import pyautogui
pyautogui.FAILSAFE=False
pyautogui.moveTo(__PIXELGYM_PARK_X__,__PIXELGYM_PARK_Y__,duration=0)
x,y=pyautogui.position()
print(json.dumps({'parked':[int(x),int(y)]},separators=(',',':')))
