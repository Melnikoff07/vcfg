"""Create/read back the title-specific marker after checking the live profile.

Plugin upload and INI changes remain the responsibility of tools/console.py set.
"""
import ftplib
import io
import json
import pathlib
import sys
import time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import console


def main():
    marker='/data/bb_probe/gridmix_CUSA00557'
    with console.connect() as ftp:
        profile=console.get(ftp,'/data/bb_probe/config-CUSA00557.ini').decode()
        values=dict(line.split('=',1) for line in profile.splitlines() if '=' in line and not line.startswith(';'))
        for key,expected in [('framegen','1'),('preset','0'),('sharpness','100'),('enhance','0')]:
            if values.get(key)!=expected:raise ValueError(f'live profile {key} must be {expected}; refusing automatic settings changes')
        created=False
        try:content=console.get(ftp,marker)
        except ftplib.error_perm as ex:
            if not str(ex).startswith('550'):raise
            content=b'gridmix experimental CUSA00557 clear profile, 2026-10-20\n'
            stage=marker+'.stage_'+time.strftime('%Y%m%d_%H%M%S')
            ftp.storbinary('STOR '+stage,io.BytesIO(content))
            if console.get(ftp,stage)!=content:raise ValueError('marker readback mismatch')
            ftp.rename(stage,marker);created=True
        if console.get(ftp,marker)!=content:raise ValueError('marker final readback mismatch')
    out=console.ROOT/'logs/gridmix_before_20261020/marker.json'
    out.write_text(json.dumps(dict(path=marker,created=created,profile=values),indent=2))
    print('Live profile matches Quality / 100 / AA off; marker readback verified.')


if __name__=='__main__':main()
