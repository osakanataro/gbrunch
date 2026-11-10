#!/usr/bin/env python3
"""Smoke-test a tools/build_profile_kernel.py output in QEMU (TCG, no hardware, no disks, no network).

  test_profile_kernel_qemu.py --build DIR --out REPORT.json [--modules NAME ...] [--cpu Skylake-Client]

Boots the kernel with a BusyBox initramfs that holds the dependency closure of the profile's first-stage
modules plus --modules, insmods them in order, runs the AVX-VNNI user test (the kernel's built-in
emulator must make a CPU model without AVX-VNNI execute VPDP* correctly) and powers off. The report
records each insmod result, the emulator counters and the user-test verdict. QEMU does not emulate
AVX-512, so only the ymm register path is exercised; the zmm path needs Ice Lake / Tiger Lake hardware.
Needs qemu-system-x86_64, a static busybox (Debian busybox-static) and gcc for the user test.
"""
import argparse,gzip,json,pathlib,re,shutil,subprocess,sys,tempfile
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
from build_profile_kernel import module_closure
from prepare_profile_assets import cpio_newc
ROOT=pathlib.Path(__file__).resolve().parents[1]

INIT='''#!/bin/busybox sh
/bin/busybox --install -s /bin
mount -t proc proc /proc; mount -t sysfs sysfs /sys
# Android kernels have no devtmpfs (ueventd populates /dev); make the console node by hand.
mount -t tmpfs tmpfs /dev; mknod /dev/ttyS0 c 4 64; mknod /dev/console c 5 1; mknod /dev/null c 1 3
exec >/dev/ttyS0 2>&1
echo "GBRUNCH-QEMU: $(uname -r)"
dmesg | grep GBRUNCH
for m in $(cat /modules/order); do
  if insmod /modules/$m; then echo "INSMOD OK $m"; else echo "INSMOD FAIL $m"; dmesg | tail -3; fi
done
[ -e /dev/ashmem ] && echo "ASHMEM device present" || echo "ASHMEM device absent (memfd shim expected)"
if [ -x /vnni_test ]; then /vnni_test; echo "vnni_test exit $?"; fi
if [ -x /ashmem_test ]; then /ashmem_test; echo "ashmem_test exit $?"; fi
echo "EMULATED $(cat /sys/module/gbrunch_avxvnni/parameters/emulated 2>/dev/null) DECLINED $(cat /sys/module/gbrunch_avxvnni/parameters/declined 2>/dev/null)"
echo GBRUNCH-QEMU-DONE
poweroff -f
'''

def main():
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--build',required=True,type=pathlib.Path);p.add_argument('--out',required=True,type=pathlib.Path)
    p.add_argument('--modules',nargs='*',default=[],help='extra module names to load (dependency closure added)')
    p.add_argument('--allow-nodev',nargs='*',default=[],help='module names whose init may fail in QEMU (DMI/ACPI match, e.g. surface_gpe)')
    p.add_argument('--cpu',default='Skylake-Client');p.add_argument('--qemu',default='qemu-system-x86_64')
    p.add_argument('--busybox',type=pathlib.Path,default=pathlib.Path('/usr/bin/busybox'));p.add_argument('--timeout',type=int,default=900)
    a=p.parse_args();build=a.build.resolve();spec=json.loads((build/'kernel.json').read_text())
    if not shutil.which(a.qemu):raise SystemExit('missing '+a.qemu)
    bb=a.busybox.read_bytes()
    if b'/lib64/ld-linux' in bb[:4096]:raise SystemExit('busybox must be statically linked')
    names=[m['name'] for m in spec['ramdisk_modules']]+a.modules
    closure=module_closure(build/'modules',spec['release'],names)
    moddir=build/'modules/lib/modules'/spec['release']
    entries=[('bin',0o40755,b''),('bin/busybox',0o100755,bb),('init',0o100755,INIT.encode()),('proc',0o40755,b''),('sys',0o40755,b''),('dev',0o40755,b''),
             ('modules',0o40755,b''),('modules/order',0o100644,'\n'.join(m['file'] for m in closure).encode()+b'\n')]
    for m in closure:entries.append(('modules/'+m['file'],0o100644,(moddir/m['path']).read_bytes()))
    with tempfile.TemporaryDirectory(prefix='gbrunch-kqemu-') as td:
        w=pathlib.Path(td);test_src=ROOT/'gki-modules/gbrunch-avxvnni/test/vnni_test.c';vnni=None
        if shutil.which('gcc'):
            r=subprocess.run(['gcc','-O2','-static','-mavx2','-mavxvnni','-o',str(w/'vnni_test'),str(test_src)],capture_output=True,text=True)
            if r.returncode==0:vnni=(w/'vnni_test').read_bytes();entries.append(('vnni_test',0o100755,vnni))
            else:print('WARNING: gcc cannot build the AVX-VNNI test; skipping it',file=sys.stderr)
        ashmem=None
        if shutil.which('gcc'):
            r=subprocess.run(['gcc','-O2','-static','-o',str(w/'ashmem_test'),str(ROOT/'gki-kernel/test/ashmem_memfd_test.c')],capture_output=True,text=True)
            if r.returncode==0:ashmem=(w/'ashmem_test').read_bytes();entries.append(('ashmem_test',0o100755,ashmem))
            else:print('WARNING: gcc cannot build the ashmem test; skipping it',file=sys.stderr)
        (w/'initrd.gz').write_bytes(gzip.compress(cpio_newc(entries),mtime=0))
        cmd=[a.qemu,'-machine','q35','-accel','tcg','-cpu',a.cpu,'-m','1024','-smp','2','-nographic','-no-reboot','-net','none',
             '-kernel',str(build/'kernel'),'-initrd',str(w/'initrd.gz'),
             '-append','console=ttyS0,115200 8250.nr_uarts=4 rdinit=/init loglevel=5 panic=-1']
        try:r=subprocess.run(cmd,capture_output=True,text=True,timeout=a.timeout,stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as e:raise SystemExit('QEMU timed out; partial output:\n'+(e.stdout or b'').decode(errors='replace')[-4000:])
    log=r.stdout;insmod={m.group(2):m.group(1) for m in re.finditer(r'^INSMOD (OK|FAIL) (\S+)',log,re.M)}
    counters=re.search(r'^EMULATED (\d*) DECLINED (\d*)',log,re.M)
    report={'kernel_sha256':spec['kernel_sha256'],'release':spec['release'],'cpu':a.cpu,'command':cmd,
            'uname':(re.search(r'GBRUNCH-QEMU: (\S+)',log) or [None,None])[1],
            'armed':bool(re.search(r'GBRUNCH: AVX-VNNI emulation armed',log)),
            'modules_requested':names,'closure':[m['file'] for m in closure],'insmod':insmod,
            'ashmem_device':'ASHMEM device present' in log,
            'vnni_test':None if vnni is None else ('PASSED' if 'vnni_test PASSED' in log else 'FAILED'),
            'ashmem_test':None if ashmem is None else ('PASSED' if 'ashmem_memfd_test PASSED' in log else 'FAILED'),
            'emulated':int(counters.group(1) or 0) if counters else None,'declined':int(counters.group(2) or 0) if counters else None,
            'completed':'GBRUNCH-QEMU-DONE' in log,'qemu_exit':r.returncode,'log_tail':log[-6000:]}
    allowed={n.replace('-','_')+'.ko' for n in a.allow_nodev}|{n+'.ko' for n in a.allow_nodev}
    report['allowed_failures']=sorted(f for f,v in insmod.items() if v!='OK' and f in allowed)
    report['passed']=(report['completed'] and report['uname']==spec['release'] and report['armed'] and bool(insmod) and all(v=='OK' or f in allowed for f,v in insmod.items())
                      and set(insmod)==set(report['closure']) and report['vnni_test'] in ('PASSED',None) and report['ashmem_test'] in ('PASSED',None) and (vnni is None or bool(report['emulated'])))
    a.out.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ['passed','uname','armed','ashmem_device','vnni_test','ashmem_test','emulated','declined','allowed_failures']}|{'insmod_ok':sum(v=='OK' for v in insmod.values()),'insmod_fail':sorted(f for f,v in insmod.items() if v!='OK')},indent=1))
    if not report['passed']:raise SystemExit(1)

if __name__=='__main__':main()
