"""Bounded console-log capture for the currently authorized interactive test."""
import argparse
import json
import pathlib
import socket
import time

ap=argparse.ArgumentParser(description=__doc__)
ap.add_argument('--out',required=True,type=pathlib.Path)
ap.add_argument('--seconds',default=1800,type=int)
args=ap.parse_args()
cfg=json.loads((pathlib.Path(__file__).resolve().parents[2]/'config.json').read_text())
args.out.parent.mkdir(parents=True,exist_ok=True)
with args.out.open('ab',buffering=0) as output:
    with socket.create_connection((cfg['ps4_host'],3232),timeout=5) as sock:
        sock.settimeout(5);deadline=time.monotonic()+args.seconds
        print('Console log connected: '+str(args.out.resolve()),flush=True)
        while time.monotonic()<deadline:
            try:data=sock.recv(65536)
            except socket.timeout:continue
            if not data:break
            output.write(data)
