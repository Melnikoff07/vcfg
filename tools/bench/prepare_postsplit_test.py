"""Back up CUSA00557's profile and stage AA1 plus an isolated split marker."""
import ftplib
import io
import json
import pathlib
import re
import sys
import time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import console


def main():
    dest=console.ROOT/'logs/postsplit_before_20261020';dest.mkdir(parents=True,exist_ok=True)
    profile='/data/bb_probe/config-CUSA00557.ini';marker='/data/bb_probe/postfix_split_CUSA00557'
    with console.connect() as ftp:
        ini=console.get(ftp,console.INI);lines=ini.splitlines()
        section=next(idx for title,idx in console.sections(lines) if title=='CUSA00557')
        paths=[lines[i].strip() for i in section[1:] if lines[i].strip() and not lines[i].lstrip().startswith((b';',b'#'))]
        expected=b'/data/GoldHEN/plugins/vcfg_classic_postfix_20261020a.prx'
        if paths!=[expected]:raise RuntimeError('CUSA00557 no longer points to measured postfix a; inspect first')
        original=console.get(ftp,profile)
        values=dict(line.split('=',1) for line in original.decode().splitlines() if '=' in line and not line.startswith(';'))
        for key,value in [('framegen','1'),('preset','0'),('sharpness','100'),('enhance','0'),('overlay','0')]:
            if values.get(key)!=value:raise RuntimeError('unexpected profile: '+key)
        updated,count=re.subn(rb'(?m)^enhance=0(?=\r?$)',b'enhance=1',original)
        if count!=1:raise RuntimeError('enhance entry not unique')
        stamp=time.strftime('%Y%m%d_%H%M%S');backup=profile+'.before_postsplit_'+stamp
        (dest/'profile_before.ini').write_bytes(original);(dest/'profile_test.ini').write_bytes(updated)
        ftp.storbinary('STOR '+backup,io.BytesIO(original))
        if console.get(ftp,backup)!=original:raise RuntimeError('profile backup readback mismatch')
        stage=profile+'.postsplit.stage_'+stamp
        ftp.storbinary('STOR '+stage,io.BytesIO(updated))
        if console.get(ftp,stage)!=updated or console.get(ftp,profile)!=original:raise RuntimeError('profile stage/concurrency mismatch')
        ftp.rename(stage,profile)
        if console.get(ftp,profile)!=updated:raise RuntimeError('profile final readback mismatch; backup '+backup)
        try:data=console.get(ftp,marker)
        except ftplib.error_perm as ex:
            if not str(ex).startswith('550'):raise
            data=b'postsplit experimental CUSA00557, raw history / immutable AA1 real+mid\n'
            stage=marker+'.stage_'+stamp;ftp.storbinary('STOR '+stage,io.BytesIO(data))
            if console.get(ftp,stage)!=data:raise RuntimeError('marker stage mismatch')
            ftp.rename(stage,marker)
        if console.get(ftp,marker)!=data:raise RuntimeError('marker readback mismatch')
    (dest/'prepare.json').write_text(json.dumps(dict(profile=profile,backup=backup,marker=marker,previous_mapping=expected.decode(),change='enhance 0 -> 1; other values preserved'),indent=2))
    print('Backed up title profile at '+backup+'; only Enhance 0 -> 1; marker staged and read back.')


if __name__=='__main__':main()
