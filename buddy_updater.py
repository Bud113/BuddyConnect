"""Buddy Connect Windows updater: HTTPS manifest, SHA-256 verification, staged replacement."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.request

MAX_BYTES=70*1024*1024

def version_tuple(s):
    if not re.fullmatch(r'\d+(?:\.\d+){1,3}',str(s)):
        raise ValueError('Invalid release version')
    parts = [int(x) for x in s.split('.')]
    return tuple((parts + [0] * 4)[:4])

def check(manifest_url,current):
    if not manifest_url.startswith('https://'):raise ValueError('HTTPS update source required')
    req=urllib.request.Request(manifest_url,headers={'User-Agent':'BuddyConnect-Updater/1'})
    with urllib.request.urlopen(req,timeout=12) as response:
        raw=response.read(8193)
    if len(raw)>8192:raise ValueError('Update manifest too large')
    data=json.loads(raw)
    if not isinstance(data,dict):raise ValueError('Invalid manifest')
    if version_tuple(data['version'])<=version_tuple(current):return None
    url=data['url'];digest=data['sha256']
    if not isinstance(url,str) or not url.startswith('https://'):raise ValueError('Download must use HTTPS')
    if not isinstance(digest,str) or not re.fullmatch('[a-fA-F0-9]{64}',digest):raise ValueError('Invalid SHA-256')
    return {'version':data['version'],'url':url,'sha256':digest.lower()}

def download(release):
    folder=Path(tempfile.mkdtemp(prefix='buddy-connect-update-'))
    target=folder/'BuddyConnect-new.exe'
    digest=hashlib.sha256();size=0
    try:
        with urllib.request.urlopen(urllib.request.Request(release['url'],headers={'User-Agent':'BuddyConnect-Updater/1'}),timeout=30) as response, target.open('wb') as out:
            while True:
                chunk=response.read(1024*1024)
                if not chunk:break
                size+=len(chunk)
                if size>MAX_BYTES:raise ValueError('Update too large')
                digest.update(chunk);out.write(chunk)
        if digest.hexdigest()!=release['sha256']:raise ValueError('SHA-256 mismatch; update rejected')
        if target.stat().st_size<1024:raise ValueError('Downloaded file is unexpectedly small')
        return target
    except Exception:
        target.unlink(missing_ok=True)
        raise

def install(staged):
    if os.name!='nt' or not getattr(sys,'frozen',False):
        raise RuntimeError('Automatic installation requires the compiled Windows BuddyConnect.exe')
    original=Path(sys.executable).resolve()
    staged=Path(staged).resolve()
    if not staged.is_file():raise FileNotFoundError(staged)
    # A separate cmd process waits for the GUI executable to exit before replacing it.
    # No admin privileges needed when the EXE is in a user-writable directory.
    script=staged.parent/'apply_update.cmd'
    script.write_text('@echo off\nsetlocal\n'
        'set "TARGET='+str(original)+'"\n'
        'set "SOURCE='+str(staged)+'"\n'
        'set /a TRIES=0\n'
        ':retry\n'
        'timeout /t 2 /nobreak >nul\n'
        'move /y "%SOURCE%" "%TARGET%" >nul 2>&1\n'
        'if not errorlevel 1 goto done\n'
        'set /a TRIES+=1\n'
        'if %TRIES% GEQ 20 goto failed\n'
        'goto retry\n'
        ':done\n'
        'start "" "%TARGET%"\n'
        'exit /b 0\n'
        ':failed\n'
        'echo Buddy Connect update failed. Close BuddyConnect.exe and try again.\n'
        'pause\n',encoding='utf-8')
    subprocess.Popen(['cmd.exe','/c',str(script)],creationflags=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0),close_fds=True)
