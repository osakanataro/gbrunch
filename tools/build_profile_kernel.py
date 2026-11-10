#!/usr/bin/env python3
"""Build a replacement kernel for a hardware profile from the exact ACK revision of the stock kernel.

  build_profile_kernel.py --work WORK --config kernel.config --vmlinux vmlinux --profile NAME --out DIR [--compile]

WORK is the tools/build_gki_modules.py work tree: source/ = git checkout of ACK
3938c8d97142d5cf31543aa96f864a2cf832dda0, clang-r584948c/ = the pinned toolchain the stock kernel was
built with (tools/prepare_amd_build_inputs.py --clang-only). The checkout itself is never modified: the
build uses a detached git worktree under DIR/source with the profile's patches applied.

The profile is profiles/NAME/kernel.json:
  release_suffix   CONFIG_LOCALVERSION, e.g. "-gbrunch-sp7" -> 6.18.21-gbrunch-sp7
  fragments        Kconfig fragments applied in order on top of the recovery's embedded config
                   (gki-kernel/common.config first, then the profile's own)
  patches          git patches applied to the worktree (gki-kernel/patches/)
  ramdisk_modules  module names the first-stage ramdisk needs (GPU); the dependency closure is
                   computed from the build's modules.dep
  required         CONFIG values that must hold after olddefconfig, else the build refuses

Output in DIR: kernel (bzImage), kernel.config, kernel.json (manifest: hashes of inputs and outputs,
release, resolved config differences), modules/lib/modules/RELEASE/ (stripped, depmod'ed) and
ramdisk-modules/ with the closure for the ramdisk in the native-assets.json module schema.
tools/prepare_profile_assets.py --kernel DIR puts these into a profile asset directory.
Nothing from the Google recovery is read except the kernel config (and vmlinux, to verify that the
config really is the embedded one).
"""
import argparse,hashlib,json,os,pathlib,re,shutil,subprocess,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
from build_amd_kernel_modules import config_values,verify_embedded_config

ROOT=pathlib.Path(__file__).resolve().parents[1]
SOURCE_COMMIT='3938c8d97142d5cf31543aa96f864a2cf832dda0'
STOCK_RELEASE_PREFIX='6.18.21'

def run(*a,**k):return subprocess.run([str(x) for x in a],check=True,**k)
def sha(path):
    with open(path,'rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def apply_fragment(values,text):
    """Kconfig fragment semantics: later lines win; '# CONFIG_X is not set' means n."""
    changed={}
    for line in text.splitlines():
        m=re.fullmatch(r'CONFIG_(\w+)=(.+)',line.strip())
        if m:changed[m.group(1)]=m.group(2)
        m=re.fullmatch(r'# CONFIG_(\w+) is not set',line.strip())
        if m:changed[m.group(1)]='n'
    values.update(changed);return changed

def render(values):
    return ''.join('# CONFIG_%s is not set\n'%k if v=='n' else 'CONFIG_%s=%s\n'%(k,v) for k,v in values.items())

def worktree(src,dest,patches):
    """Detached worktree of the ACK commit with the patches applied. On --resume, patches added to the
    profile since the worktree was made are applied on top; a removed or changed patch needs a fresh --out."""
    git=lambda *a,**k:subprocess.run(['git','-C',str(src),*a],check=True,text=True,capture_output=True,**k)
    applied_file=dest.parent/'applied-patches.json';wanted=[{'file':str(p.relative_to(ROOT)),'sha256':sha(p)} for p in patches]
    if dest.exists():
        head=subprocess.check_output(['git','-C',str(dest),'rev-parse','HEAD'],text=True).strip()
        if head!=SOURCE_COMMIT:raise SystemExit('existing worktree is not at '+SOURCE_COMMIT)
        applied=json.loads(applied_file.read_text()) if applied_file.exists() else []
        if wanted[:len(applied)]!=applied:raise SystemExit('patch set changed (removed or edited patch); use a fresh --out')
        todo=patches[len(applied):]
    else:
        git('worktree','prune')
        git('worktree','add','--detach',str(dest),SOURCE_COMMIT);todo=patches
    for p in todo:
        subprocess.run(['git','-C',str(dest),'apply','--check',str(p)],check=True)
        subprocess.run(['git','-C',str(dest),'apply',str(p)],check=True)
    # Stage so that `git status` shows the patch set as the only difference from the commit.
    subprocess.run(['git','-C',str(dest),'add','-A'],check=True)
    applied_file.write_text(json.dumps(wanted,indent=1)+'\n')

def module_closure(moddir,release,names):
    """native-assets.json module entries (name, file, sha256, depends, aliases) for names + dependencies."""
    base=moddir/'lib/modules'/release
    deps={};order=[]
    for line in (base/'modules.dep').read_text().splitlines():
        if not line.strip():continue
        f,_,d=line.partition(':');deps[pathlib.PurePosixPath(f).name]=(f,d.split())
    byname={re.sub(r'\.ko$','',k).replace('-','_'):k for k in deps}
    def add(fname):
        if fname in order:return
        for d in deps[fname][1]:add(pathlib.PurePosixPath(d).name)
        order.append(fname)
    for n in names:
        key=n.replace('-','_')
        if key not in byname:raise SystemExit('ramdisk module not built: '+n)
        add(byname[key])
    aliases={}
    for line in (base/'modules.alias').read_text().splitlines():
        parts=line.split()
        if len(parts)==3 and parts[0]=='alias':aliases.setdefault(parts[2].replace('-','_'),[]).append(parts[1])
    out=[]
    for f in order:
        name=re.sub(r'\.ko$','',f).replace('-','_')
        out.append({'name':name,'file':f,'path':deps[f][0],'sha256':sha(base/deps[f][0]),
                    'depends':[re.sub(r'\.ko$','',pathlib.PurePosixPath(d).name).replace('-','_') for d in deps[f][1]],
                    'aliases':aliases.get(name,[])})
    return out

def main():
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--work',required=True,type=pathlib.Path)
    p.add_argument('--config',required=True,type=pathlib.Path,help="the recovery kernel's embedded config (extract-ikconfig)")
    p.add_argument('--vmlinux',required=True,type=pathlib.Path,help='extracted stock vmlinux, to verify --config')
    p.add_argument('--profile',required=True)
    p.add_argument('--out',required=True,type=pathlib.Path)
    p.add_argument('--jobs',type=int,default=os.cpu_count())
    p.add_argument('--compile',action='store_true',help='build bzImage and modules after configuring')
    p.add_argument('--resume',action='store_true',help='reuse an existing output directory of this profile')
    a=p.parse_args()
    work=a.work.resolve();out=a.out.resolve();src=work/'source';clang=work/'clang-r584948c/bin'
    profile_dir=ROOT/'profiles'/a.profile;spec=json.loads((profile_dir/'kernel.json').read_text())
    head=subprocess.check_output(['git','-C',str(src),'rev-parse','HEAD'],text=True).strip()
    if head!=SOURCE_COMMIT:raise SystemExit('work/source is not ACK '+SOURCE_COMMIT)
    marker=out/'gbrunch-kernel-build.json'
    if out.exists() and not (a.resume and marker.is_file() and json.loads(marker.read_text()).get('profile')==a.profile):
        raise SystemExit('output exists; choose a new directory or --resume the same profile')
    if shutil.disk_usage(out.parent if not out.exists() else out).free<(1 if a.resume else 8)*1024**3:
        raise SystemExit('need at least 8 GiB free for a kernel build (1 GiB to resume)')
    config=a.config.read_text();verify_embedded_config(a.vmlinux.read_bytes(),config)
    env=dict(os.environ,PATH=str(clang)+os.pathsep+os.environ['PATH'],
             KBUILD_BUILD_TIMESTAMP='Thu Jan  1 00:00:00 UTC 2026',KBUILD_BUILD_USER='gbrunch',KBUILD_BUILD_HOST='gbrunch')
    compiler=subprocess.check_output([str(clang/'clang'),'--version'],text=True).splitlines()[0]
    stock_cc=config_values(config)['CC_VERSION_TEXT'].strip('"')
    if compiler!=stock_cc:raise SystemExit('compiler differs from the stock kernel: %s vs %s'%(compiler,stock_cc))
    patches=[ROOT/x for x in spec.get('patches',[])];fragments=[ROOT/x for x in spec['fragments']]
    for f in patches+fragments:
        if not f.is_file():raise SystemExit('missing '+str(f))
    out.mkdir(parents=True,exist_ok=True);worktree(src,out/'source',patches)
    values=config_values(config);requested={}
    for f in fragments:requested.update(apply_fragment(values,f.read_text()))
    values['LOCALVERSION']='"%s"'%spec['release_suffix'];values['LOCALVERSION_AUTO']='n'
    requested['LOCALVERSION']=values['LOCALVERSION'];requested['LOCALVERSION_AUTO']='n'
    build=out/'build';build.mkdir(exist_ok=True);(build/'.config').write_text(render(values))
    mk=['make','-C',out/'source','O='+str(build),'LLVM=1','ARCH=x86_64']
    report={'profile':a.profile,'source_commit':SOURCE_COMMIT,'compiler':compiler,
            'patches':[{'file':str(x.relative_to(ROOT)),'sha256':sha(x)} for x in patches],
            'fragments':[{'file':str(x.relative_to(ROOT)),'sha256':sha(x)} for x in fragments],
            'stock_config_sha256':hashlib.sha256(config.encode()).hexdigest(),'boot_tested':False,'android_tested':False,'hardware_tested':False}
    def save():marker.write_text(json.dumps(report,indent=2)+'\n')
    save()
    with (out/'configure.log').open('wb') as log:
        r=subprocess.run([*map(str,mk),'olddefconfig'],env=env,stdout=log,stderr=subprocess.STDOUT)
    if r.returncode:raise SystemExit('olddefconfig failed; see '+str(out/'configure.log'))
    stock=config_values(config);resolved=config_values((build/'.config').read_text())
    report['resolved_diff']={k:[stock.get(k,'n'),resolved.get(k,'n')] for k in sorted(stock.keys()|resolved.keys()) if stock.get(k,'n')!=resolved.get(k,'n')}
    report['requested_not_resolved']={k:[v,resolved.get(k,'n')] for k,v in requested.items() if resolved.get(k,'n')!=v}
    missing={k:[v,resolved.get(k,'n')] for k,v in spec.get('required',{}).items() if resolved.get(k,'n')!=v}
    # setlocalversion appends '+' to any checkout that is not at an annotated tag, so the release is
    # pinned explicitly (as tools/build_gki_modules.py does for the stock release).
    version=subprocess.check_output([*map(str,mk),'-s','kernelversion'],env=env,text=True).strip()
    release=version+spec['release_suffix'];mk.append('KERNELRELEASE='+release)
    report['release']=release;save()
    if missing:raise SystemExit('required configuration did not resolve: '+json.dumps(missing))
    if not re.fullmatch(re.escape(STOCK_RELEASE_PREFIX)+r'-[a-z0-9-]+',release):raise SystemExit('unexpected kernel release '+release)
    shutil.copyfile(build/'.config',out/'kernel.config')
    print(json.dumps({'configured':True,'release':release,'changes':len(report['resolved_diff']),'requested_not_resolved':report['requested_not_resolved']},indent=1))
    if not a.compile:return
    with (out/'compile.log').open('ab') as log:
        r=subprocess.run([*map(str,mk),'-j%d'%a.jobs,'bzImage','modules'],env=env,stdout=log,stderr=subprocess.STDOUT)
    if r.returncode:raise SystemExit('compile failed; see '+str(out/'compile.log'))
    moddir=out/'modules'
    if moddir.exists():shutil.rmtree(moddir)
    with (out/'compile.log').open('ab') as log:
        r=subprocess.run([*map(str,mk),'INSTALL_MOD_PATH='+str(moddir),'INSTALL_MOD_STRIP=1','modules_install'],env=env,stdout=log,stderr=subprocess.STDOUT)
    if r.returncode:raise SystemExit('modules_install failed; see '+str(out/'compile.log'))
    for link in ('build','source'):(moddir/'lib/modules'/release/link).unlink(missing_ok=True)
    shutil.copyfile(build/'arch/x86/boot/bzImage',out/'kernel')
    closure=module_closure(moddir,release,spec.get('ramdisk_modules',[]))
    rd=out/'ramdisk-modules'
    if rd.exists():shutil.rmtree(rd)
    rd.mkdir()
    for m in closure:shutil.copyfile(moddir/'lib/modules'/release/m['path'],rd/m['file'])
    allmods=sorted(x.relative_to(moddir/'lib/modules'/release) for x in moddir.rglob('*.ko'))
    report.update({'kernel_sha256':sha(out/'kernel'),'kernel_bytes':(out/'kernel').stat().st_size,'config_sha256':sha(out/'kernel.config'),
                   'ramdisk_modules':closure,'module_count':len(allmods),'modules':{str(x):sha(moddir/'lib/modules'/release/x) for x in allmods}})
    save();shutil.copyfile(marker,out/'kernel.json')
    print(json.dumps({'compiled':True,'release':release,'kernel_sha256':report['kernel_sha256'],'modules':len(allmods),'ramdisk_modules':[m['file'] for m in closure]},indent=1))

if __name__=='__main__':main()
