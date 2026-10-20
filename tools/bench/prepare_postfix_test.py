"""Check the live profile/mapping and create an isolated title marker, with readback."""
import ftplib
import io
import json
import pathlib
import sys
import time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import console


def main():
    marker='/data/bb_probe/postfix_CUSA00557'
    dest=console.ROOT/'logs/postfix_before_20261020';dest.mkdir(parents=True,exist_ok=True)
    with console.connect() as ftp:
        ini=console.get(ftp,console.INI);lines=ini.splitlines()
        section=next(idx for title,idx in console.sections(lines) if title=='CUSA00557')
        paths=[lines[i].strip() for i in section[1:] if lines[i].strip() and not lines[i].lstrip().startswith((b';',b'#'))]
        expected=b'/data/GoldHEN/plugins/vcfg_classic_gridmix_20261020b.prx'
        if paths!=[expected]:raise RuntimeError('mapping differs from measured gridmix b: inspect before staging')
        content=console.get(ftp,'/data/bb_probe/config-CUSA00557.ini').decode()
        values=dict(line.split('=',1) for line in content.splitlines() if '=' in line and not line.startswith(';'))
        for key,value in [('framegen','1'),('preset','0'),('sharpness','100'),('enhance','0'),('overlay','0')]:
            if values.get(key)!=value:raise RuntimeError(f'profile {key} must be {value}; no automatic profile changes')
        created=False
        try:data=console.get(ftp,marker)
        except ftplib.error_perm as ex:
            if not str(ex).startswith('550'):raise
            data=b'postfix experimental CUSA00557, raw-real / immutable post, 2026-10-20\n'
            stage=marker+'.stage_'+time.strftime('%Y%m%d_%H%M%S')
            ftp.storbinary('STOR '+stage,io.BytesIO(data))
            if console.get(ftp,stage)!=data:raise RuntimeError('marker stage mismatch')
            ftp.rename(stage,marker);created=True
        if console.get(ftp,marker)!=data:raise RuntimeError('marker readback mismatch')
    (dest/'prepare.json').write_text(json.dumps(dict(marker=marker,created=created,profile=values,prior_mapping=expected.decode()),indent=2))
    print('Verified gridmix b mapping and unchanged Quality / 100 / AA-off / overlay-off profile. POSTFIX marker read back.')


if __name__=='__main__':main()
