import os,subprocess,time
env=os.environ.copy()
env['DISPLAY']=':0'
last='not attempted'
for attempt in range(480):
 try:
  result=subprocess.run(['wmctrl','-m'],env=env,capture_output=True,text=True,timeout=2)
  last=(result.stdout+result.stderr).strip()
  if result.returncode == 0:
   print('DESKTOP_READY')
   break
 except Exception as exc:
  last=repr(exc)
 if attempt == 479: raise RuntimeError(last)
 time.sleep(0.25)
