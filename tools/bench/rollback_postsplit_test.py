"""Restore postfix a and its exact profile after the split-AA experiment."""
import ftplib
import io
import pathlib
import sys
import time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import console

dest=console.ROOT/'logs/postsplit_before_20261020'
before=(dest/'profile_before.ini').read_bytes();tested=(dest/'profile_test.ini').read_bytes()
profile='/data/bb_probe/config-CUSA00557.ini'
with console.connect() as ftp:
    original=console.get(ftp,console.INI);lines=original.splitlines()
    section=next(idx for title,idx in console.sections(lines) if title=='CUSA00557')
    body=[i for i in section[1:] if lines[i].strip() and not lines[i].lstrip().startswith((b';',b'#'))]
    if len(body)!=1:raise RuntimeError('unexpected plugin mapping')
    current=lines[body[0]].strip();previous=b'/data/GoldHEN/plugins/vcfg_classic_postfix_20261020a.prx'
    if current!=previous and not current.startswith(b'/data/GoldHEN/plugins/vcfg_classic_postsplit_20261020'):
        raise RuntimeError('mapping changed outside this test')
    live=console.get(ftp,profile)
    if live not in (before,tested):raise RuntimeError('profile changed outside this test')
    ftp.size(previous.decode());lines[body[0]]=previous
    console.write_ini(ftp,original,lines,'postsplit_rollback_CUSA00557')
    if live!=before:
        backup=profile+'.before_postsplit_rollback_'+time.strftime('%Y%m%d_%H%M%S')
        ftp.storbinary('STOR '+backup,io.BytesIO(live))
        if console.get(ftp,backup)!=live:raise RuntimeError('rollback backup mismatch')
        stage=profile+'.rollback.stage';ftp.storbinary('STOR '+stage,io.BytesIO(before))
        if console.get(ftp,stage)!=before or console.get(ftp,profile)!=live:raise RuntimeError('rollback stage/concurrency mismatch')
        ftp.rename(stage,profile)
        if console.get(ftp,profile)!=before:raise RuntimeError('rollback final mismatch')
    marker='/data/bb_probe/postfix_split_CUSA00557'
    try:console.get(ftp,marker)
    except ftplib.error_perm as ex:
        if not str(ex).startswith('550'):raise
    else:
        quarantine='/data/vcfg_quarantine_20261019_163934/postfix_split_CUSA00557_'+time.strftime('%Y%m%d_%H%M%S')
        ftp.rename(marker,quarantine);print('MARKER_QUARANTINED',quarantine)
    print('Restored postfix a and its exact pre-test profile; no artifacts deleted.')
