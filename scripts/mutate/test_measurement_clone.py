"""A local partial cache must be hydrated before gix reads its history."""
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]


def git(repo,*args):
    return subprocess.check_output(['git','-C',str(repo),*args],text=True,stderr=subprocess.DEVNULL).strip()


def missing(repo):
    return sum(line.startswith('?') for line in git(repo,'rev-list','--objects','--missing=print','HEAD').splitlines())


def test_measurement_clone_hydrates_partial_source_without_mutating_it(tmp_path):
    server=tmp_path/'server'
    server.mkdir()
    git(server,'init','-b','main')
    git(server,'config','user.name','Test')
    git(server,'config','user.email','test@example.com')
    git(server,'config','uploadpack.allowFilter','true')
    for i in range(4):
        (server/'file').write_text(str(i)*500)
        git(server,'add','.')
        git(server,'commit','-m',str(i))
    sha=git(server,'rev-parse','HEAD')
    source=tmp_path/'.cache/repos/sample'
    source.parent.mkdir(parents=True)
    subprocess.check_call(['git','clone','--filter=blob:none','--no-checkout',server.as_uri(),str(source)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    assert missing(source)>0
    script=(ROOT/'scripts/measure_bootstrap_w6.sh').read_text()
    function=script[script.index('ensure_repo() {'):script.index('\nparse_dry_total()')]
    dest=tmp_path/'measured'
    dest.mkdir()
    program=f'MEASURE_REPOS={str(dest)!r}\nset -euo pipefail\n'+function+f'\nensure_repo sample {server.as_uri()!r} {sha!r}\n'
    result=subprocess.run(['bash','-c',program],cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert missing(dest/'sample')==0
    assert git(dest/'sample','rev-parse','HEAD')==sha
    assert missing(source)>0,'original cache must stay untouched'
