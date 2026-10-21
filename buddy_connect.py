"""Buddy Connect v8.1: Windows settings dashboard and localhost-only background agent."""
import json
import urllib.request
import os
import re
import socket
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tkinter import messagebox, ttk, filedialog, colorchooser
import calendar

APP_VERSION='8.3.0'
PORT=8765
RUN_KEY=r'Software\Microsoft\Windows\CurrentVersion\Run'
NAME='BuddyConnect'
DEFAULT={'name':'Buddy','color':'#62d9ff','shape':'rounded','mood':'happy','idle':True,'sound':True,'sleep':True,'sleepDelay':'60','clock24':False,'showName':True,'showPageDots':True,'blink':True,'showClockDate':True}
VALID={'shape':('rounded','circle','square'),'mood':('happy','neutral','sleepy','sad','surprised','angry'),'sleepDelay':('30','60','120','300')}
DATA_DIR=Path(os.getenv('APPDATA',str(Path.home()))) / 'BuddyConnect'
DATA_FILE=DATA_DIR/'settings.json'
state_lock=threading.RLock()

def validate(raw):
    if not isinstance(raw,dict): raise ValueError('Settings must be an object')
    out={}
    for key,default in DEFAULT.items():
        val=raw.get(key,default)
        if isinstance(default,bool):
            if not isinstance(val,bool): raise ValueError('Invalid '+key)
        elif not isinstance(val,str): raise ValueError('Invalid '+key)
        if key=='name': val=val[:18].strip() or 'Buddy'
        if key=='color' and not re.fullmatch(r'#[0-9a-fA-F]{6}',val): raise ValueError('Invalid color')
        if key in VALID and val not in VALID[key]: raise ValueError('Invalid '+key)
        out[key]=val
    return out

def load_state():
    try:
        data=json.loads(DATA_FILE.read_text(encoding='utf-8'))
        return {'settings':validate(data['settings']),'revision':int(data.get('revision',0))}
    except (OSError,ValueError,TypeError,KeyError):
        return {'settings':dict(DEFAULT),'revision':0}

def save_state(s):
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    tmp=DATA_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(s,indent=2),encoding='utf-8')
    os.replace(tmp,DATA_FILE)

state=load_state()
APP_DEFAULT={'reminders':[],'events':[],'alarms':[],'notes':[],'timerEnd':0,'timerLeft':300,'timerRunning':False}
APP_FILE=DATA_DIR/'organizer.json'
def validate_apps(raw):
    if not isinstance(raw,dict):raise ValueError('Invalid organizer data')
    result=dict(APP_DEFAULT)
    for key in ('reminders','events','alarms','notes'):
        items=raw.get(key,[])
        if not isinstance(items,list) or len(items)>500:raise ValueError('Invalid '+key)
        cleaned=[]
        for item in items:
            if not isinstance(item,dict) or not isinstance(item.get('text'),str):raise ValueError('Invalid item')
            entry={'id':str(item.get('id',''))[:90], 'text':item['text'][:1500 if key=='notes' else 90]}
            if not entry['text'].strip():continue
            if key!='notes':
                when=item.get('when')
                if not isinstance(when,(int,float)) or not 0<=when<=9999999999999:raise ValueError('Invalid date')
                entry.update(when=when,done=bool(item.get('done',False)))
            cleaned.append(entry)
        result[key]=cleaned
    for k in ('timerEnd','timerLeft'):
        v=raw.get(k,APP_DEFAULT[k]);result[k]=v if isinstance(v,(int,float)) else APP_DEFAULT[k]
    result['timerRunning']=bool(raw.get('timerRunning',False))
    return result
def load_apps():
    try:
        obj=json.loads(APP_FILE.read_text(encoding='utf-8'))
        return {'data':validate_apps(obj['data']),'revision':int(obj.get('revision',0))}
    except (OSError,ValueError,KeyError,TypeError):return {'data':dict(APP_DEFAULT),'revision':0}
def save_apps_state(obj):
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    tmp=APP_FILE.with_suffix('.tmp');tmp.write_text(json.dumps(obj,indent=2),encoding='utf-8');os.replace(tmp,APP_FILE)
apps_state=load_apps()


def exe_cmd():
    return [sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).resolve())]

def agent_running():
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/health', timeout=.8) as response:
            return response.status == 200 and json.load(response).get('app') == 'BuddyConnect'
    except (OSError, ValueError):
        return False

def wifi_signal():
    if os.name!='nt':return None,None
    try:
        p=subprocess.run(['netsh','wlan','show','interfaces'],capture_output=True,text=True,timeout=3,errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        m=re.search(r'^\s*Signal\s*:\s*(\d{1,3})\s*%',p.stdout,re.I|re.M)
        if not m:return None,None
        n=max(0,min(100,int(m.group(1))))
        return n,(0 if n==0 else min(4,(n+24)//25))
    except (OSError,subprocess.TimeoutExpired,ValueError):return None,None

def run_agent():
    import psutil
    # Single sampling loop: the desktop UI and simulator read this same snapshot.
    # A 250ms cadence is responsive without running blocking CPU measurements per request.
    stats_lock=threading.Lock()
    snapshot={'cpu':0.0,'ram':0.0,'disk':None,'temp':None,'battery':None,
              'plugged':None,'wifi':'Unknown','wifi_signal':None,'wifi_bars':None,'sample_id':0}
    def sample_stats():
        psutil.cpu_percent(interval=None)  # Prime this process's CPU counter.
        count=0
        while True:
            try:
                cpu=psutil.cpu_percent(interval=None)
                mem=psutil.virtual_memory().percent
                data={'cpu':round(cpu,1),'ram':round(mem,1)}
                # Slower-changing measurements are refreshed once every ~2 seconds.
                if count%8==0:
                    data['disk']=psutil.disk_usage(os.environ.get('SystemDrive','C:')+'\\' if os.name=='nt' else '/').percent
                    try:
                        temps=psutil.sensors_temperatures() or {}
                        data['temp']=next((x.current for group in temps.values() for x in group if x.current is not None),None)
                    except (AttributeError,OSError,RuntimeError):data['temp']=None
                    try:
                        bat=psutil.sensors_battery()
                        data['battery']=round(bat.percent) if bat else None
                        data['plugged']=bool(bat.power_plugged) if bat else None
                    except (OSError,RuntimeError,AttributeError):data['battery']=None;data['plugged']=None
                    try:
                        interfaces=psutil.net_if_stats();active=[name for name,info in interfaces.items() if info.isup]
                        wireless=[name for name in active if any(word in name.lower() for word in ('wi-fi','wifi','wireless','wlan'))]
                        data['wifi']='Connected' if wireless else ('Not connected' if active else 'Offline')
                    except Exception:data['wifi']='Unknown'
                    data['wifi_signal'],data['wifi_bars']=wifi_signal()
                with stats_lock:
                    snapshot.update(data)
                    snapshot['sample_id']+=1
            except Exception:
                pass
            count+=1
            threading.Event().wait(.25)
    threading.Thread(target=sample_stats,daemon=True,name='BuddyStatsSampler').start()
    def refresh_saved_state():
        # GUI and agent are separate processes: pick up settings saved by either.
        latest=load_state()
        with state_lock:
            if latest['revision'] > state['revision']:
                state.update(latest)
    def refresh_apps():
        latest=load_apps()
        with state_lock:
            if latest['revision']>apps_state['revision']:apps_state.update(latest)
    class Handler(BaseHTTPRequestHandler):
        def respond(self,code,obj):
            payload=json.dumps(obj).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin','null' if self.headers.get('Origin')=='null' else 'http://localhost')
            self.send_header('Vary','Origin')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(payload)))
            self.end_headers();self.wfile.write(payload)
        def origin_ok(self):
            origin=self.headers.get('Origin')
            return origin in (None,'null','http://localhost','http://127.0.0.1')
        def do_OPTIONS(self):
            if not self.origin_ok():self.send_error(403);return
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin',self.headers.get('Origin','null'))
            self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers','Content-Type, X-Buddy-Client')
            self.end_headers()
        def do_GET(self):
            if not self.origin_ok():self.send_error(403);return
            route=self.path.split('?')[0]
            if route=='/health':
                self.respond(200,{'app':'BuddyConnect','version':APP_VERSION})
                return
            if route=='/apps':
                refresh_apps()
                with state_lock:self.respond(200,dict(apps_state))
                return
            if route=='/settings':
                refresh_saved_state()
                with state_lock:self.respond(200,dict(state))
                return
            if route!='/stats':self.send_error(404);return
            with stats_lock:
                self.respond(200,dict(snapshot))
        def do_POST(self):
            if not self.origin_ok() or self.headers.get('X-Buddy-Client')!='simulator':self.send_error(403);return
            route=self.path.split('?')[0]
            if route not in ('/settings','/apps'):self.send_error(404);return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=300000:raise ValueError('Invalid size')
                payload=json.loads(self.rfile.read(length))
                if route=='/apps':
                    values=validate_apps(payload['data']);refresh_apps()
                    with state_lock:
                        apps_state['data']=values;apps_state['revision']+=1;save_apps_state(apps_state)
                        result=dict(apps_state)
                    self.respond(200,result);return
                values=validate(payload['settings'])
                refresh_saved_state()
                with state_lock:
                    state['settings']=values;state['revision']+=1;save_state(state)
                    result=dict(state)
                self.respond(200,result)
            except (ValueError,KeyError,TypeError,UnicodeDecodeError) as exc:self.respond(400,{'error':str(exc)})
        def log_message(self,*args):pass
    try:server=ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
    except OSError:return
    server.serve_forever()

def enable_autostart():
    import winreg
    cmd=subprocess.list2cmdline(exe_cmd()+['--agent'])
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,RUN_KEY) as k:winreg.SetValueEx(k,NAME,0,winreg.REG_SZ,cmd)

def disable_autostart():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,RUN_KEY,0,winreg.KEY_SET_VALUE) as k:winreg.DeleteValue(k,NAME)
    except FileNotFoundError:pass

def start_agent():
    if agent_running():return
    flags=getattr(subprocess,'DETACHED_PROCESS',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)
    command=exe_cmd()+['--agent']
    if not getattr(sys,'frozen',False) and sys.platform=='win32':
        # pythonw.exe avoids a console window when running from source.
        pythonw=Path(sys.executable).with_name('pythonw.exe')
        if pythonw.exists():command[0]=str(pythonw)
    subprocess.Popen(command,creationflags=flags,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True)

def app():
    import datetime
    import buddy_updater
    import uuid
    import customtkinter as ctk
    from PIL import Image
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    root=ctk.CTk();root.title('Buddy Connect  •  v'+APP_VERSION);root.geometry('940x720');root.minsize(840,650)
    try:
        icon_path=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))/'buddy_connect.ico'
        if icon_path.exists():root.iconbitmap(str(icon_path))
    except (OSError,tk.TclError):pass
    BG='#101827';SIDE='#101827';CARD='#1e2b3e';FG='#e7eaf0';MUTED='#a6afbe';ACC='#58d7ff'
    root.configure(bg=BG)
    style=ttk.Style(root);style.theme_use('clam')
    style.configure('TCombobox',fieldbackground='#21334a',background='#21334a',foreground=FG,padding=5)
    style.map('TCombobox',fieldbackground=[('readonly','#21334a')],foreground=[('readonly',FG)])
    def parent_bg(parent):
        # Standard Tk widgets embedded in CTk frames need the visible frame color.
        try:
            color=parent.cget('fg_color')
            if isinstance(color,(tuple,list)):color=color[-1]
            if color not in (None,'transparent'):return color
        except (ValueError,tk.TclError,AttributeError):pass
        try:return parent.cget('bg')
        except (ValueError,tk.TclError):return BG
    def label(parent,text,size=10,color=FG,bold=False,**kw):
        bg=parent_bg(parent)
        return tk.Label(parent,text=text,bg=bg,fg=color,font=('Segoe UI',size,'bold' if bold else 'normal'),**kw)
    def btn(parent,text,cmd,primary=False):
        return ctk.CTkButton(parent,text=text,command=cmd,corner_radius=12,height=37,
            fg_color=ACC if primary else '#294660',hover_color='#7be4ff' if primary else '#365a79',
            text_color=BG if primary else FG,font=ctk.CTkFont(family='Segoe UI',size=12,weight='bold'))
    # Screenshot-inspired shell: compact branding, two-by-two navigation, single Buddy card.
    mast=ctk.CTkFrame(root,fg_color=BG,corner_radius=0,height=69)
    mast.pack(fill='x',padx=8);mast.pack_propagate(False)
    logo=ctk.CTkFrame(mast,width=44,height=44,fg_color='#1c354a',corner_radius=13)
    logo.pack(side='left',padx=(0,10),pady=9);logo.pack_propagate(False)
    try:
        logo_path=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))/'buddy_connect_icon.png'
        logo_image=ctk.CTkImage(light_image=Image.open(logo_path),dark_image=Image.open(logo_path),size=(40,40))
        logo_label=ctk.CTkLabel(logo,text='',image=logo_image,width=40,height=40)
        logo_label.pack(expand=True)
    except (OSError,ValueError):
        ctk.CTkLabel(logo,text='●  ●',text_color=ACC,width=40,height=40).pack(expand=True)
    brand_text=ctk.CTkFrame(mast,fg_color=BG,corner_radius=0);brand_text.pack(side='left',pady=8)
    label(brand_text,'Buddy Connect',12,FG,True).pack(anchor='w')
    label(brand_text,'Your desktop companion',8,MUTED).pack(anchor='w')
    connection=tk.StringVar(value='Offline')
    connection_badge=ctk.CTkLabel(mast,textvariable=connection,fg_color='#682d37',text_color='#ffb9c0',corner_radius=12,font=ctk.CTkFont(size=12),width=95,height=27)
    connection_badge.pack(side='right',padx=5)
    ctk.CTkFrame(root,fg_color='#2a374a',corner_radius=0,height=1).pack(fill='x',padx=8)
    main=ctk.CTkFrame(root,fg_color=BG,corner_radius=0);main.pack(fill='both',expand=True)
    navstrip=ctk.CTkFrame(main,fg_color=BG,corner_radius=0);navstrip.pack(fill='x',padx=8,pady=(19,12))
    for col in (0,1):navstrip.grid_columnconfigure(col,weight=1,uniform='nav')
    content=ctk.CTkFrame(main,fg_color=BG,corner_radius=0);content.pack(fill='both',expand=True,padx=8)
    footer=ctk.CTkFrame(main,fg_color=BG,corner_radius=0);footer.pack(fill='x',padx=8,pady=(8,11))
    info=tk.StringVar(value='All changes save automatically')
    pages={};nav={}
    icons={'Home':'⌂','Appearance':'◉','Settings':'☷','Organizer':'▦','PC Stats':'▥'}
    for title in ('Home','Appearance','Settings','Organizer','PC Stats','Behavior','Connection'):
        pages[title]=ctk.CTkFrame(content,fg_color=BG,corner_radius=0)
        if title in ('Behavior','Connection'):continue
        i={'Home':0,'Appearance':1,'Settings':2,'Organizer':3,'PC Stats':4}[title]
        tile=ctk.CTkButton(navstrip,text=icons[title]+'   '+title,anchor='w',height=46,
            corner_radius=13,fg_color=CARD,hover_color='#2b425c',text_color=FG,
            font=ctk.CTkFont(family='Segoe UI',size=14,weight='bold'))
        tile.grid(row=i//2,column=i%2,sticky='ew',padx=(0,4) if i%2==0 else (4,0),pady=(0,7))
        nav[title]=tile
    settings_tab_buttons={}
    def show(name):
        for f in pages.values():f.pack_forget()
        if name=='Settings':
            pages['Settings'].pack(fill='x')
            pages['Behavior'].pack(fill='x')
        elif name=='Advanced':
            pages['Settings'].pack(fill='x')
            pages['Connection'].pack(fill='x')
        else:pages[name].pack(fill='both',expand=True)
        for key,tile in nav.items():
            tile.configure(fg_color='#25374d' if key==name or (name=='Advanced' and key=='Settings') else CARD)
        if settings_tab_buttons:
            for tab,button in settings_tab_buttons.items():
                selected=(name=='Settings' and tab=='Settings') or (name=='Advanced' and tab=='Advanced')
                button.configure(fg_color=ACC if selected else '#294660',
                    hover_color='#7be4ff' if selected else '#365a79',
                    text_color=BG if selected else FG)
        if name=='Organizer':render_organizer()
    for name,tile in nav.items():tile.configure(command=lambda n=name:show(n))
    def panel(parent,title):
        outer=ctk.CTkFrame(parent,fg_color=CARD,corner_radius=17)
        outer.pack(fill='x',pady=(0,12))
        label(outer,title,12,FG,True).pack(anchor='w',padx=18,pady=(15,10))
        return outer
    def line(parent,title):
        row=ctk.CTkFrame(parent,fg_color=CARD,corner_radius=0)
        row.pack(fill='x',padx=18,pady=7)
        label(row,title,10,FG).pack(side='left');return row
    # Friendly settings tabs, with advanced connection controls tucked away.
    settings_tabs=ctk.CTkFrame(pages['Settings'],fg_color=BG,corner_radius=0)
    settings_tabs.pack(anchor='w',fill='x',pady=(0,14))
    buddy_tab=btn(settings_tabs,'Buddy settings',lambda:show('Settings'),True)
    buddy_tab.pack(side='left',padx=(0,8))
    connection_tab=btn(settings_tabs,'Connection & updates',lambda:show('Advanced'))
    connection_tab.pack(side='left')
    settings_tab_buttons.update({'Settings':buddy_tab,'Advanced':connection_tab})
    # Live PC statistics use the same psutil source as the local Buddy agent.
    stats_page=pages['PC Stats']
    stats_header=ctk.CTkFrame(stats_page,fg_color=BG,corner_radius=0)
    stats_header.pack(fill='x',pady=(0,12))
    ctk.CTkLabel(stats_header,text='PC Stats',font=ctk.CTkFont(family='Segoe UI',size=19,weight='bold'),text_color=FG).pack(side='left')
    stats_status=tk.StringVar(value='Reading your computer...')
    ctk.CTkLabel(stats_header,textvariable=stats_status,text_color=MUTED,font=ctk.CTkFont(size=11)).pack(side='right')
    stats_grid=ctk.CTkFrame(stats_page,fg_color=BG,corner_radius=0)
    stats_grid.pack(fill='both',expand=True)
    for c in (0,1):stats_grid.grid_columnconfigure(c,weight=1,uniform='stat')
    for r in (0,1,2):stats_grid.grid_rowconfigure(r,weight=1)
    stat_fields={}
    for i,(key,heading,detail) in enumerate((
        ('cpu','CPU usage','Processor load'),('ram','Memory usage','System RAM'),
        ('disk','Storage usage','System drive'),('battery','Battery','Power status'),
        ('temp','CPU temperature','If supported'),('wifi','Wi-Fi','Connection status'))):
        tile=ctk.CTkFrame(stats_grid,fg_color=CARD,corner_radius=18)
        tile.grid(row=i//2,column=i%2,sticky='nsew',padx=(0,5) if i%2==0 else (5,0),pady=(0,10))
        ctk.CTkLabel(tile,text=heading,text_color=MUTED,font=ctk.CTkFont(size=12)).pack(anchor='w',padx=17,pady=(13,0))
        value=ctk.CTkLabel(tile,text='—',text_color=ACC,font=ctk.CTkFont(family='Segoe UI',size=25,weight='bold'))
        value.pack(anchor='w',padx=17,pady=(2,0))
        ctk.CTkLabel(tile,text=detail,text_color=MUTED,font=ctk.CTkFont(size=10)).pack(anchor='w',padx=17,pady=(0,10))
        stat_fields[key]=value
    def refresh_pc_stats():
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/stats',timeout=.45) as response:
                data=json.load(response)
            cpu=data.get('cpu');mem=data.get('ram');disk=data.get('disk')
            bat=data.get('battery');plugged=data.get('plugged')
            temp=data.get('temp');bars=data.get('wifi_bars')
            wifi=f'{bars}/4 bars' if bars is not None else data.get('wifi','N/A')
            values={'cpu':f'{cpu:.0f}%' if cpu is not None else 'N/A',
                    'ram':f'{mem:.0f}%' if mem is not None else 'N/A',
                    'disk':f'{disk:.0f}%' if disk is not None else 'N/A',
                    'battery':(f'{bat:.0f}%'+(' · Charging' if plugged else '')) if bat is not None else 'N/A',
                    'temp':f'{temp:.0f}°C' if temp is not None else 'N/A','wifi':wifi}
            for k,v in values.items():stat_fields[k].configure(text=v)
            stats_status.set('Live · shared with Buddy · 0.25s sampling')
        except Exception as exc:
            stats_status.set('Waiting for Buddy Connect agent: '+str(exc)[:45])
        root.after(250,refresh_pc_stats)
    root.after(600,refresh_pc_stats)
    # Home is a single card, matching the supplied visual reference.
    dash=pages['Home']
    home_card=ctk.CTkFrame(dash,fg_color=CARD,corner_radius=19);home_card.pack(fill='both',expand=True)
    top=ctk.CTkFrame(home_card,fg_color=CARD,corner_radius=0);top.pack(fill='x',padx=14,pady=(14,12))
    label(top,'Your Buddy',11,FG,True).pack(side='left')
    label(top,'Live preview',9,ACC).pack(side='right')
    preview_bg=ctk.CTkFrame(home_card,fg_color='#0b1220',corner_radius=15);preview_bg.pack(fill='both',expand=True,padx=13)
    face=tk.Canvas(preview_bg,bg='#0b1220',highlightthickness=0,height=285)
    face.pack(fill='both',expand=True,padx=12,pady=12)
    connection_row=ctk.CTkFrame(home_card,fg_color=CARD,corner_radius=0);connection_row.pack(fill='x',padx=14,pady=(12,14))
    left_status=ctk.CTkFrame(connection_row,fg_color=CARD,corner_radius=0);left_status.pack(side='left',fill='x',expand=True)
    label(left_status,'Connection',10,FG,True).pack(anchor='w')
    status_text=tk.StringVar(value='Connect Buddy to get started')
    tk.Label(left_status,textvariable=status_text,bg=CARD,fg=MUTED,font=('Segoe UI',9)).pack(anchor='w',pady=(3,0))
    home_connection_badge=ctk.CTkLabel(connection_row,text='Offline',fg_color='#682d37',text_color='#ffb9c0',corner_radius=12,width=74,height=27,font=ctk.CTkFont(size=12))
    home_connection_badge.pack(side='right')
    def connect():
        try:enable_autostart();start_agent();root.after(1000,refresh_connection);info.set('Connected. Background startup enabled.')
        except Exception as e:messagebox.showerror('Connection error',str(e))
    connect_button=btn(connection_row,'Connect Buddy',connect,True)
    connect_button.pack(side='right',padx=(0,9))
    fields={}
    def draw_preview():
        face.delete('all')
        source={**state['settings'],**{k:v.get() for k,v in fields.items()}}
        col=source['color']
        if not re.fullmatch(r'#[0-9a-fA-F]{6}',str(col)):col='#62d9ff'
        mood=source['mood'];shape=source['shape']
        width=max(face.winfo_width(),380);height=max(face.winfo_height(),260)
        cx=width/2;cy=height/2-6
        # Flat cyan outline, two eyes and a smile; scale with the available preview area.
        scale=min((width-50)/340,(height-62)/200,1.8)
        x0,y0,x1,y1=cx-165*scale,cy-94*scale,cx+165*scale,cy+94*scale
        r=26*scale
        # Draw a large, smooth Buddy frame using four curved corners.
        face.create_polygon(x0+r,y0,x1-r,y0,x1,y0+r,x1,y1-r,x1-r,y1,x0+r,y1,x0,y1-r,x0,y0+r,
            fill='#101e30',outline=col,width=4,smooth=True,splinesteps=32)
        for x in (cx-58*scale,cx+58*scale):
            if mood=='sleepy':face.create_line(x-14*scale,cy-9*scale,x+14*scale,cy-9*scale,fill=col,width=max(3,int(6*scale)),capstyle='round')
            elif shape=='square':face.create_rectangle(x-14*scale,cy-24*scale,x+14*scale,cy+22*scale,fill=col,outline='')
            elif shape=='circle':face.create_oval(x-16*scale,cy-16*scale,x+16*scale,cy+16*scale,fill=col,outline='')
            else:face.create_oval(x-14*scale,cy-25*scale,x+14*scale,cy+25*scale,fill=col,outline='')
        if mood=='surprised':face.create_oval(cx-9*scale,cy+32*scale,cx+9*scale,cy+52*scale,outline=col,width=4)
        elif mood=='sad':face.create_arc(cx-28*scale,cy+39*scale,cx+28*scale,cy+72*scale,start=15,extent=150,style='arc',outline=col,width=4)
        elif mood=='happy':face.create_arc(cx-28*scale,cy+18*scale,cx+28*scale,cy+57*scale,start=190,extent=160,style='arc',outline=col,width=4)
        else:face.create_line(cx-24*scale,cy+43*scale,cx+24*scale,cy+43*scale,fill=col,width=4,capstyle='round')
        face.create_text(cx,height-16,text=source['name']+' is '+mood+'!',fill='#c8d0df',font=('Segoe UI',9))
    face.bind('<Configure>',lambda _:draw_preview())
    draw_preview()
    # Settings autosave with debounce
    saving=[None];updating=[False]
    def persist_settings():
        saving[0]=None
        if updating[0]:return
        try:
            new=validate({k:v.get() for k,v in fields.items()})
            latest=load_state()
            with state_lock:
                state['settings']=new;state['revision']=max(state['revision'],latest['revision'])+1;save_state(state)
            draw_preview();info.set('All changes saved automatically')
        except (ValueError,OSError) as e:info.set('Check setting: '+str(e))
    def changed(*_):
        if updating[0]:return
        draw_preview()
        info.set('Saving...')
        if saving[0] is not None:root.after_cancel(saving[0])
        saving[0]=root.after(500,persist_settings)
    def setting(parent,key,title,kind='entry',choices=None):
        row=line(parent,title);default=state['settings'][key]
        v=tk.BooleanVar(value=default) if isinstance(default,bool) else tk.StringVar(value=default)
        fields[key]=v
        if key=='color':
            def pick_color():
                picked=colorchooser.askcolor(color=v.get(),title='Choose Buddy eye color',parent=root)
                if picked and picked[1]:v.set(picked[1])
            w=ctk.CTkButton(row,textvariable=v,command=pick_color,fg_color='#294660',corner_radius=10,width=120)
            w.pack(side='right',padx=3)
            swatch=ctk.CTkButton(row,text='',fg_color=v.get(),command=pick_color,corner_radius=10,width=38)
            swatch.pack(side='right',padx=5)
            def sync_swatch(*_):
                if re.fullmatch(r'#[0-9a-fA-F]{6}',v.get()):swatch.configure(fg_color=v.get())
            v.trace_add('write',sync_swatch)
            row.bind('<Button-1>',lambda _:pick_color())
        else:
            if kind=='check':w=ctk.CTkSwitch(row,text='',variable=v,progress_color=ACC,button_color=FG,fg_color='#315c77',width=45)
            elif kind=='select':w=ctk.CTkOptionMenu(row,variable=v,values=list(choices),corner_radius=10,fg_color='#294660',button_color='#365a79',width=160)
            else:w=ctk.CTkEntry(row,textvariable=v,fg_color='#0e1c2d',text_color=FG,corner_radius=10,width=165)
            w.pack(side='right',padx=3)
        v.trace_add('write',changed)
    a=panel(pages['Appearance'],'Customize Buddy')
    setting(a,'name','Buddy name');setting(a,'color','Eye color')
    setting(a,'shape','Eye shape','select',VALID['shape']);setting(a,'mood','Default expression','select',VALID['mood'])
    setting(a,'showName','Show name on Buddy','check')
    b=panel(pages['Behavior'],'Display & interaction')
    setting(b,'idle','Idle animations','check');setting(b,'blink','Eye blinking','check')
    setting(b,'sound','Sound effects','check');setting(b,'sleep','Automatic sleep','check')
    setting(b,'sleepDelay','Sleep delay (seconds)','select',VALID['sleepDelay'])
    setting(b,'clock24','24-hour clock','check');setting(b,'showClockDate','Date on clock screen','check')
    setting(b,'showPageDots','Page navigation dots','check')
    def open_datetime_picker(entry):
        try:chosen=datetime.datetime.strptime(entry.get().strip(),'%Y-%m-%d %H:%M')
        except ValueError:chosen=datetime.datetime.now().replace(second=0,microsecond=0)
        popup=tk.Toplevel(root);popup.title('Choose date and time');popup.configure(bg=BG)
        popup.transient(root);popup.resizable(False,False)
        popup.grab_set()
        month=[chosen.year,chosen.month];selected=[chosen.day]
        title_var=tk.StringVar()
        heading_row=tk.Frame(popup,bg=BG);heading_row.pack(fill='x',padx=14,pady=(12,6))
        cal_frame=tk.Frame(popup,bg=BG);cal_frame.pack(padx=14)
        def change_month(offset):
            total=month[0]*12+month[1]-1+offset
            month[0],index=divmod(total,12);month[1]=index+1
            selected[0]=min(selected[0],calendar.monthrange(month[0],month[1])[1]);render_calendar()
        tk.Button(heading_row,text='‹',command=lambda:change_month(-1),bg=CARD,fg=FG,relief='flat',width=4).pack(side='left')
        tk.Label(heading_row,textvariable=title_var,bg=BG,fg=FG,font=('Segoe UI',12,'bold'),width=23).pack(side='left')
        tk.Button(heading_row,text='›',command=lambda:change_month(1),bg=CARD,fg=FG,relief='flat',width=4).pack(side='left')
        def select_day(day):selected[0]=day;render_calendar()
        def render_calendar():
            for child in cal_frame.winfo_children():child.destroy()
            title_var.set(datetime.date(month[0],month[1],1).strftime('%B %Y'))
            for col,day in enumerate(('Mo','Tu','We','Th','Fr','Sa','Su')):
                tk.Label(cal_frame,text=day,bg=BG,fg=MUTED,width=5).grid(row=0,column=col,pady=3)
            for r,week in enumerate(calendar.monthcalendar(*month),1):
                for c,day in enumerate(week):
                    if not day:continue
                    active=day==selected[0]
                    tk.Button(cal_frame,text=str(day),width=4,relief='flat',cursor='hand2',
                        bg=ACC if active else CARD,fg=BG if active else FG,
                        command=lambda d=day:select_day(d)).grid(row=r,column=c,padx=2,pady=2)
        render_calendar()
        time_row=tk.Frame(popup,bg=BG);time_row.pack(pady=(12,8))
        tk.Label(time_row,text='Time:',bg=BG,fg=FG).pack(side='left',padx=7)
        hour=tk.Spinbox(time_row,from_=0,to=23,wrap=True,width=4,format='%02.0f',font=('Segoe UI',12),justify='center')
        minute=tk.Spinbox(time_row,from_=0,to=59,wrap=True,width=4,format='%02.0f',font=('Segoe UI',12),justify='center')
        hour.delete(0,'end');hour.insert(0,f'{chosen.hour:02d}')
        minute.delete(0,'end');minute.insert(0,f'{chosen.minute:02d}')
        hour.pack(side='left');tk.Label(time_row,text=':',bg=BG,fg=FG).pack(side='left');minute.pack(side='left')
        def apply():
            try:date=datetime.datetime(month[0],month[1],selected[0],int(hour.get()),int(minute.get()))
            except ValueError:messagebox.showerror('Invalid time','Enter a valid hour (0–23) and minute (0–59).',parent=popup);return
            entry.delete(0,'end');entry.insert(0,date.strftime('%Y-%m-%d %H:%M'));popup.destroy()
        tk.Button(popup,text='Use this date and time',command=apply,bg=ACC,fg=BG,relief='flat',padx=15,pady=9).pack(pady=(3,15))
        popup.bind('<Return>',lambda _:apply())
    # Organizer: four large rounded cards, no tab strip or stacked form rectangles.
    org=pages['Organizer']
    org_heading=ctk.CTkFrame(org,fg_color=BG,corner_radius=0)
    org_heading.pack(fill='x',pady=(0,12))
    ctk.CTkLabel(org_heading,text='Your organizer',font=ctk.CTkFont(family='Segoe UI',size=21,weight='bold'),text_color=FG).pack(side='left')
    ctk.CTkLabel(org_heading,text='Everything in one place',font=ctk.CTkFont(size=12),text_color=MUTED).pack(side='right')
    org_grid=ctk.CTkFrame(org,fg_color=BG,corner_radius=0)
    org_grid.pack(fill='both',expand=True)
    org_grid.grid_columnconfigure((0,1),weight=1,uniform='organizer')
    org_grid.grid_rowconfigure((0,1),weight=1)
    org_kinds={'Reminders':'reminders','Alarms':'alarms','Calendar':'events','Notes':'notes'}
    org_cards={}
    def persist_apps():
        latest=load_apps()
        with state_lock:
            apps_state['revision']=max(apps_state['revision'],latest['revision'])+1
            save_apps_state(apps_state)
        info.set('Organizer saved and synchronized')
    def remove_item(key,item_id):
        apps_state['data'][key]=[o for o in apps_state['data'][key] if str(o['id'])!=str(item_id)]
        persist_apps();render_organizer()
    def add_organizer_item(key):
        popup=ctk.CTkToplevel(root)
        popup.title('Add '+('calendar event' if key=='events' else key[:-1]))
        popup.geometry('470x390' if key=='notes' else '470x330')
        popup.resizable(False,False);popup.transient(root);popup.grab_set()
        wrap=ctk.CTkFrame(popup,fg_color=CARD,corner_radius=20)
        wrap.pack(fill='both',expand=True,padx=14,pady=14)
        ctk.CTkLabel(wrap,text='New '+('calendar event' if key=='events' else key[:-1]),text_color=FG,font=ctk.CTkFont(size=21,weight='bold')).pack(anchor='w',padx=20,pady=(20,12))
        if key=='notes':
            entry=ctk.CTkTextbox(wrap,corner_radius=12,fg_color='#101827',text_color=FG,height=160)
        else:
            entry=ctk.CTkEntry(wrap,placeholder_text='What would you like to remember?',corner_radius=12,height=43,fg_color='#101827',text_color=FG)
        entry.pack(fill='x',padx=20)
        date=None
        if key!='notes':
            row=ctk.CTkFrame(wrap,fg_color=CARD,corner_radius=0)
            row.pack(fill='x',padx=20,pady=(18,0))
            date=ctk.CTkEntry(row,corner_radius=10,height=38,fg_color='#101827',text_color=FG)
            date.insert(0,(datetime.datetime.now()+datetime.timedelta(hours=1)).strftime('%Y-%m-%d %H:%M'))
            date.pack(side='left',fill='x',expand=True,padx=(0,8))
            ctk.CTkButton(row,text='Pick date',width=98,corner_radius=10,command=lambda:open_datetime_picker(date)).pack(side='right')
            date.bind('<Button-1>',lambda _:open_datetime_picker(date))
        def save():
            value=entry.get('1.0','end').strip() if key=='notes' else entry.get().strip()
            if not value:messagebox.showwarning('Missing text','Enter a title or note.',parent=popup);return
            item={'id':uuid.uuid4().hex,'text':value[:1500 if key=='notes' else 90]}
            if key!='notes':
                try:item.update(when=int(datetime.datetime.strptime(date.get().strip(),'%Y-%m-%d %H:%M').timestamp()*1000),done=False)
                except ValueError:messagebox.showerror('Invalid date','Choose a valid date and time.',parent=popup);return
            apps_state['data'][key].append(item)
            persist_apps();render_organizer();popup.destroy()
        ctk.CTkButton(wrap,text='Save',command=save,corner_radius=12,height=41,fg_color=ACC,hover_color='#7be4ff',text_color=BG).pack(side='bottom',fill='x',padx=20,pady=20)
    for i,(title,key) in enumerate(org_kinds.items()):
        card=ctk.CTkFrame(org_grid,fg_color=CARD,corner_radius=20)
        card.grid(row=i//2,column=i%2,sticky='nsew',padx=(0,7) if i%2==0 else (7,0),pady=(0,7) if i<2 else (7,0))
        heading=ctk.CTkFrame(card,fg_color='transparent')
        heading.pack(fill='x',padx=17,pady=(16,7))
        ctk.CTkLabel(heading,text=title,text_color=FG,font=ctk.CTkFont(size=17,weight='bold')).pack(side='left')
        ctk.CTkButton(heading,text='+ Add',command=lambda k=key:add_organizer_item(k),width=70,height=31,corner_radius=12,fg_color='#315673',hover_color='#3c6d8c').pack(side='right')
        count=ctk.CTkLabel(card,text='',text_color=MUTED,font=ctk.CTkFont(size=11))
        count.pack(anchor='w',padx=17,pady=(0,6))
        listing=ctk.CTkScrollableFrame(card,fg_color='transparent',corner_radius=12)
        listing.pack(fill='both',expand=True,padx=12,pady=(0,12))
        org_cards[key]=(count,listing)
    def render_organizer():
        for key,(count,parent) in org_cards.items():
            for child in parent.winfo_children():child.destroy()
            items=apps_state['data'][key]
            if key!='notes':items=sorted(items,key=lambda x:x['when'])
            count.configure(text=f'{len(items)} saved '+('item' if len(items)==1 else 'items'))
            if not items:
                ctk.CTkLabel(parent,text='Nothing here yet. Click + Add to begin.',text_color=MUTED,font=ctk.CTkFont(size=11),wraplength=280).pack(anchor='w',padx=5,pady=10)
            for item in items[:100]:
                row=ctk.CTkFrame(parent,fg_color='#26374c',corner_radius=13)
                row.pack(fill='x',pady=4)
                ctk.CTkButton(row,text='×',width=28,height=28,corner_radius=9,fg_color='transparent',hover_color='#633541',text_color='#ffb9c0',command=lambda k=key,i=item['id']:remove_item(k,i)).pack(side='right',padx=8,pady=8)
                body=ctk.CTkFrame(row,fg_color='transparent')
                body.pack(side='left',fill='x',expand=True,padx=11,pady=9)
                ctk.CTkLabel(body,text=item['text'][:110],text_color=FG,font=ctk.CTkFont(size=12),wraplength=260,justify='left',anchor='w').pack(anchor='w')
                if key!='notes':
                    stamp=datetime.datetime.fromtimestamp(item['when']/1000).strftime('%b %d, %Y · %I:%M %p')
                    ctk.CTkLabel(body,text=stamp,text_color=MUTED,font=ctk.CTkFont(size=10)).pack(anchor='w')
    # Connection page
    cp=panel(pages['Connection'],'Connection & advanced options')
    label(cp,'Runs automatically when you sign in to Windows. You may close this window.',10,MUTED,wraplength=650).pack(anchor='w')
    btn(cp,'Connect & enable startup',connect,True).pack(anchor='w',pady=10)
    def disable():
        try:disable_autostart();info.set('Startup disabled. Current agent keeps running until restart.')
        except Exception as e:messagebox.showerror('Error',str(e))
    btn(cp,'Disable automatic startup',disable).pack(anchor='w')
    label(cp,'Buddy OS browser simulator must run on this PC. Physical ESP32 firmware is not included.',10,MUTED,wraplength=620).pack(anchor='w',pady=(16,0))
    # Automatically discover the newest GitHub release. No editable URL field.
    up=panel(pages['Connection'],'Updates')
    label(up,'Automatically checks GitHub for the newest Buddy Connect release.',10,MUTED).pack(anchor='w')
    update_status=tk.StringVar(value='Click Check for updates to check GitHub.')
    tk.Label(up,textvariable=update_status,bg=CARD,fg=MUTED,wraplength=620,justify='left').pack(anchor='w',pady=8)
    def check_updates():
        update_status.set('Checking for updates...')
        def work():
            try:
                import buddy_updater
                result=buddy_updater.check_latest(APP_VERSION)
                def finished():
                    if result is None:update_status.set('Buddy Connect is up to date (v'+APP_VERSION+').');return
                    update_status.set('Update v'+result['version']+' available.')
                    if messagebox.askyesno('Buddy Connect update','Download and install v'+result['version']+'?\n\nBuddy Connect will close to install.'):
                        update_status.set('Downloading and verifying update...')
                        def download():
                            try:
                                staged=buddy_updater.download(result)
                                root.after(0,lambda: install(staged))
                            except Exception as e:root.after(0,lambda msg=str(e):update_status.set('Update failed: '+msg))
                        threading.Thread(target=download,daemon=True).start()
                root.after(0,finished)
            except Exception as e:root.after(0,lambda msg=str(e):update_status.set('Update check failed: '+msg))
        threading.Thread(target=work,daemon=True).start()
    def install(staged):
        try:
            import buddy_updater
            buddy_updater.install(staged)
            root.destroy()
        except Exception as e:messagebox.showerror('Update error',str(e))
    btn(up,'Check for updates',check_updates,True).pack(anchor='w',pady=6)

    def refresh_connection():
        running=agent_running();connection.set('Connected' if running else 'Offline')
        connection_badge.configure(fg_color='#123f30' if running else '#682d37',text_color='#2cdb80' if running else '#ffb9c0')
        home_connection_badge.configure(text='Online' if running else 'Offline',fg_color='#123f30' if running else '#682d37',text_color='#2cdb80' if running else '#ffb9c0')
        if running:connect_button.pack_forget()
        elif not connect_button.winfo_manager():connect_button.pack(side='right',padx=(0,9))
        status_text.set('Everything is working' if running else 'Click Connect Buddy to get started')
    def refresh_remote():
        # Refresh only when no pending GUI edit, to avoid losing user input.
        if saving[0] is None:
            remote=load_state()
            if remote['revision']>state['revision']:
                with state_lock:state.update(remote)
                updating[0]=True
                try:
                    for key,var in fields.items():var.set(state['settings'][key])
                finally:updating[0]=False
                draw_preview()
        latest=load_apps()
        if latest['revision']>apps_state['revision']:
            apps_state.update(latest)
            if pages['Organizer'].winfo_manager():render_organizer()
        refresh_connection();root.after(2000,refresh_remote)
    # Beginner onboarding. The Test Tutorial button can replay it at any time.
    tutorial_pages=[
        ('Welcome to Buddy Connect','This app helps you customize Buddy and keep your reminders, alarms and notes in sync.','Next: connect Buddy'),
        ('Connect Buddy','Choose Connect Buddy on the Home page. Buddy Connect will run in the background when you sign in to Windows.','Next: make it yours'),
        ('Make Buddy yours','Open Appearance to choose an eye color, expression and Buddy name. Your changes save automatically.','Next: stay organized'),
        ('Everything in one place','Use Organizer for reminders, alarms, calendar events and notes. Settings has updates and connection options.','Finish')]
    def open_tutorial():
        win=ctk.CTkToplevel(root);win.title('Welcome to Buddy Connect');win.geometry('510x365')
        win.resizable(False,False);win.configure(bg=BG);win.transient(root);win.grab_set()
        idx=[0]
        label(win,'●  Buddy Connect',16,ACC,True).pack(anchor='w',padx=28,pady=(25,10))
        title_label=label(win,'',19,FG,True);title_label.pack(anchor='w',padx=28,pady=(8,10))
        desc_label=label(win,'',11,MUTED,wraplength=445,justify='left');desc_label.pack(anchor='w',padx=28)
        step_label=label(win,'',9,MUTED);step_label.pack(anchor='w',padx=28,pady=(20,0))
        actions=tk.Frame(win,bg=BG);actions.pack(side='bottom',fill='x',padx=28,pady=25)
        back=btn(actions,'Back',lambda:step(-1));back.pack(side='left')
        nxt=btn(actions,'Next',lambda:step(1),True);nxt.pack(side='right')
        def step(delta=0):
            idx[0]=max(0,min(len(tutorial_pages)-1,idx[0]+delta))
            title,description,action=tutorial_pages[idx[0]]
            title_label.config(text=title);desc_label.config(text=description)
            step_label.config(text=f'Step {idx[0]+1} of {len(tutorial_pages)}')
            back.configure(state='normal' if idx[0]>0 else 'disabled')
            nxt.configure(text='Finish' if idx[0]==len(tutorial_pages)-1 else 'Next',
                command=win.destroy if idx[0]==len(tutorial_pages)-1 else lambda:step(1))
        step()
    # Keep first-run onboarding, without a replay/test button.
    tk.Label(footer,text='Buddy Connect v'+APP_VERSION,bg=BG,fg=MUTED,font=('Segoe UI',9)).pack(side='left')
    ctk.CTkButton(footer,text='↻  Check for updates',command=lambda:show('Advanced'),fg_color='transparent',hover_color='#1e2b3e',text_color=ACC,corner_radius=10,width=150,height=30).pack(side='right')
    show('Home');refresh_remote()
    # Only auto-show once per user.
    tutorial_flag=DATA_DIR/'tutorial_v8.2_seen.txt'
    if not tutorial_flag.exists():
        try:
            DATA_DIR.mkdir(parents=True,exist_ok=True)
            tutorial_flag.write_text('1',encoding='utf-8')
            root.after(500,open_tutorial)
        except OSError:pass
    root.mainloop()

if __name__=='__main__':
    if '--agent' in sys.argv:run_agent()
    elif sys.platform!='win32':print('Buddy Connect desktop interface is designed for Windows.')
    else:app()
