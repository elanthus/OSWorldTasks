import os,re,subprocess
env=os.environ.copy(); env['DISPLAY']=':0'
query=subprocess.run(['xrandr','--query'],env=env,capture_output=True,text=True,timeout=5,check=True).stdout
outputs=[line.split()[0] for line in query.splitlines() if ' connected' in line]
if len(outputs) != 1: raise RuntimeError(outputs)
subprocess.run(['xrandr','--output',outputs[0],'--mode','__PIXELGYM_DISPLAY_WIDTH__x__PIXELGYM_DISPLAY_HEIGHT__','--scale','1x1','--panning','__PIXELGYM_DISPLAY_WIDTH__x__PIXELGYM_DISPLAY_HEIGHT__','--fb','__PIXELGYM_DISPLAY_WIDTH__x__PIXELGYM_DISPLAY_HEIGHT__'],env=env,capture_output=True,text=True,timeout=10,check=True)
verified=subprocess.run(['xrandr','--current'],env=env,capture_output=True,text=True,timeout=5,check=True).stdout
if re.search(r'current __PIXELGYM_DISPLAY_WIDTH__ x __PIXELGYM_DISPLAY_HEIGHT__',verified) is None: raise RuntimeError(verified)
print('DISPLAY_SIZE_READY')
