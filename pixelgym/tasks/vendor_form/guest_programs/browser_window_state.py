import json,os,re,subprocess,urllib.request
env=os.environ.copy(); env['DISPLAY']=':0'
def run(argv):
 result=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=5)
 if result.returncode != 0: raise RuntimeError(result.stderr.strip() or argv[0])
 return result.stdout.strip()
active_raw=run(['xprop','-root','_NET_ACTIVE_WINDOW'])
match=re.search(r'0x[0-9a-fA-F]+',active_raw)
if match is None: raise RuntimeError(active_raw)
active_id=match.group(0).lower()
windows=[]
for line in run(['wmctrl','-lGx']).splitlines():
 parts=line.split(None,7)
 if len(parts) != 8: continue
 windows.append({'id':parts[0].lower(),'desktop':parts[1],'x':int(parts[2]),'y':int(parts[3]),'width':int(parts[4]),'height':int(parts[5]),'class':parts[6],'title':parts[7]})
properties=run(['xprop','-id',active_id,'_NET_WM_STATE','WM_CLASS','_NET_WM_NAME'])
page_ready=json.load(urllib.request.urlopen('__PIXELGYM_APP_URL__api/page-ready',timeout=5))
print(json.dumps({'active_window_id':active_id,'windows':windows,'active_window_properties':properties,'task_app_page_ready':page_ready},sort_keys=True))
