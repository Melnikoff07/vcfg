"""Restore only CUSA00557's pre-test mapping; retain all console artifacts."""
import pathlib
import sys
import time
import ftplib
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import console

with console.connect() as ftp:
    original=console.get(ftp,console.INI)
    lines=original.splitlines()
    section=next(idx for title,idx in console.sections(lines) if title=='CUSA00557')
    body=[i for i in section[1:] if lines[i].strip() and not lines[i].lstrip().startswith((b';',b'#'))]
    if len(body)!=1:raise RuntimeError('unexpected CUSA00557 mapping; refusing')
    current=lines[body[0]].strip()
    old=b'/data/GoldHEN/plugins/vcfg_classic_cut2.prx'
    if current != old and not current.startswith(b'/data/GoldHEN/plugins/vcfg_classic_gridmix_20261020'):
        raise RuntimeError('mapping changed outside this test; refusing')
    ftp.size(old.decode())
    lines[body[0]]=old
    console.write_ini(ftp,original,lines,'gridmix_rollback_CUSA00557')
    marker='/data/bb_probe/gridmix_CUSA00557'
    try:console.get(ftp,marker)
    except ftplib.error_perm as ex:
        if not str(ex).startswith('550'):raise
    else:
        target='/data/vcfg_quarantine_20261019_163934/gridmix_CUSA00557_'+time.strftime('%Y%m%d_%H%M%S')
        ftp.rename(marker,target)
        print('MARKER_QUARANTINED',target)
    print('CUSA00557 restored to cut2; PRX retained.')
