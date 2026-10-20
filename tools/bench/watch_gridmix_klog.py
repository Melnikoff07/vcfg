"""Record the console log during an interactive gridmix hardware test."""
import json
import pathlib
import socket
import time
root=pathlib.Path(__file__).resolve().parents[2]
cfg=json.loads((root/'config.json').read_text())
host=cfg.get('console_host','192.168.1.21')
dest=root/'logs/gridmix_before_20261020/hardware_klog.txt'
dest.parent.mkdir(parents=True,exist_ok=True)
with dest.open('ab',buffering=0) as output:
    with socket.create_connection((host,3232),timeout=5) as sock:
        sock.settimeout(5)
        print('Console log connected: '+str(dest),flush=True)
        deadline=time.monotonic()+1800
        while time.monotonic()<deadline:
            try:data=sock.recv(65536)
            except socket.timeout:continue
            if not data:break
            output.write(data)
