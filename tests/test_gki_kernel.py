#!/usr/bin/env python3
"""Replacement-kernel profile plumbing: patches, fragments, module closures and asset derivation.

Everything runs on synthetic inputs: no kernel build, no network, no Google bytes.
"""
import gzip,hashlib,json,os,pathlib,re,shutil,subprocess,sys,tempfile,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
os.environ['PATH']+=os.pathsep+'/sbin'+os.pathsep+'/usr/sbin'
import build_profile_kernel as bpk
import prepare_profile_assets as ppa

sha=lambda b:hashlib.sha256(b).hexdigest()
TOOLS=all(shutil.which(t) for t in ['mke2fs','debugfs','e2fsck'])

class PatchAndFragmentTests(unittest.TestCase):
    def test_builtin_emulator_patch_matches_module_source(self):
        patch=(ROOT/'gki-kernel/patches/0002-x86-gbrunch-avxvnni-builtin.patch').read_text()
        body=patch.split('+++ b/arch/x86/kernel/gbrunch_avxvnni.c\n',1)[1].split('\ndiff --git',1)[0]
        added=''.join(l[1:]+'\n' for l in body.splitlines()[1:] if l.startswith('+'))
        self.assertEqual(added,(ROOT/'gki-modules/gbrunch-avxvnni/gbrunch_avxvnni.c').read_text())
        self.assertIn('obj-$(CONFIG_GBRUNCH_AVXVNNI_EMU)',patch);self.assertIn('config GBRUNCH_AVXVNNI_EMU',patch)
    def test_ashmem_shim_patch_hooks_shmem(self):
        patch=(ROOT/'gki-kernel/patches/0001-mm-memfd-ashmem-shim.patch').read_text()
        self.assertIn('+config MEMFD_ASHMEM_SHIM',patch);self.assertIn('+++ b/mm/memfd-ashmem-shim.c',patch)
        self.assertEqual(patch.count('+#if IS_ENABLED(CONFIG_ASHMEM) || IS_ENABLED(CONFIG_MEMFD_ASHMEM_SHIM)'),3)
        self.assertIn('SPDX-License-Identifier: GPL-2.0',patch)
    def test_profile_kernel_specs_reference_existing_files(self):
        for spec_file in ROOT.glob('profiles/*/kernel.json'):
            spec=json.loads(spec_file.read_text())
            self.assertRegex(spec['release_suffix'],r'^-gbrunch-[a-z0-9-]+$',spec_file)
            self.assertEqual(spec['fragments'][0],'gki-kernel/common.config')
            for rel in spec['fragments']+spec['patches']:self.assertTrue((ROOT/rel).is_file(),rel)
            for rel in spec['fragments']:
                for line in (ROOT/rel).read_text().splitlines():
                    if line and not line.startswith('#') or line.startswith('# CONFIG_'):
                        self.assertRegex(line,r'^(CONFIG_\w+=\S+|# CONFIG_\w+ is not set)$',rel+': '+line)
            self.assertIn('GBRUNCH_AVXVNNI_EMU',spec['required']);self.assertEqual(spec['required']['RUST'],'n')
    def test_fragment_semantics(self):
        values={'A':'y','B':'m','C':'"x"'}
        changed=bpk.apply_fragment(values,'# comment\nCONFIG_A=n\n# CONFIG_B is not set\nCONFIG_D="q r"\nCONFIG_A=m\n')
        self.assertEqual(values,{'A':'m','B':'n','C':'"x"','D':'"q r"'});self.assertEqual(changed,{'A':'m','B':'n','D':'"q r"'})
        self.assertEqual(bpk.render({'A':'m','B':'n'}),'CONFIG_A=m\n# CONFIG_B is not set\n')
    def test_common_fragment_has_the_non_negotiables(self):
        values={}
        bpk.apply_fragment(values,(ROOT/'gki-kernel/common.config').read_text())
        self.assertEqual(values['RUST'],'n');self.assertEqual(values['MEMFD_ASHMEM_SHIM'],'y')
        self.assertEqual(values['GBRUNCH_AVXVNNI_EMU'],'y');self.assertEqual(values['MODULE_SIG_PROTECT_LIST'],'""')
        self.assertEqual(values['DMABUF_HEAPS_SYSTEM'],'y');self.assertEqual(values['SERIAL_8250_RUNTIME_UARTS'],'4')

def fake_kernel_build(root,profile,release='6.18.21-gbrunch-test'):
    """A tools/build_profile_kernel.py --compile output with three modules: gpu -> helper, wifi -> helper."""
    base=root/'modules/lib/modules'/release;(base/'kernel/drivers').mkdir(parents=True)
    kos={'helper.ko':b'helper-module','gpu-drv.ko':b'gpu-module','wifi.ko':b'wifi-module'}
    for n,b in kos.items():(base/'kernel/drivers'/n).write_bytes(b)
    (base/'modules.dep').write_text('kernel/drivers/helper.ko:\nkernel/drivers/gpu-drv.ko: kernel/drivers/helper.ko\nkernel/drivers/wifi.ko: kernel/drivers/helper.ko\n')
    (base/'modules.alias').write_text('alias pci:v00008086d00008A52sv*sd*bc*sc*i* gpu_drv\nalias pci:v00008086d000034F0sv*sd*bc*sc*i* wifi\n')
    closure=bpk.module_closure(root/'modules',release,['gpu_drv'])
    (root/'ramdisk-modules').mkdir()
    for m in closure:shutil.copyfile(base/m['path'],root/'ramdisk-modules'/m['file'])
    (root/'kernel').write_bytes(b'bzImage-bytes');(root/'kernel.config').write_text('CONFIG_X=y\n')
    spec={'profile':profile,'release':release,'source_commit':bpk.SOURCE_COMMIT,'kernel_sha256':sha(b'bzImage-bytes'),
          'patches':[{'file':'gki-kernel/patches/0001-mm-memfd-ashmem-shim.patch','sha256':'0'*64}],'fragments':[],'ramdisk_modules':closure}
    (root/'kernel.json').write_text(json.dumps(spec))
    return spec

class ModuleClosureTests(unittest.TestCase):
    def test_closure_is_dependency_first_with_aliases(self):
        with tempfile.TemporaryDirectory() as d:
            spec=fake_kernel_build(pathlib.Path(d),'t')
            self.assertEqual([m['file'] for m in spec['ramdisk_modules']],['helper.ko','gpu-drv.ko'])
            gpu=spec['ramdisk_modules'][1]
            self.assertEqual(gpu['name'],'gpu_drv');self.assertEqual(gpu['depends'],['helper']);self.assertEqual(gpu['sha256'],sha(b'gpu-module'))
            self.assertEqual(gpu['aliases'],['pci:v00008086d00008A52sv*sd*bc*sc*i*'])
            with self.assertRaises(SystemExit):bpk.module_closure(pathlib.Path(d)/'modules','6.18.21-gbrunch-test',['missing'])

INIT_RC='''import /system/etc/init/hw/init.rc

on early-init
    write /dev/kmsg "GBRUNCH: independent compatibility init loaded"
'''+ppa.INIT_ANCHOR+'''    insmod /oem/modules/ac.ko
    insmod /vendor/lib/modules/system_heap.ko

on post-fs-data
    insmod /system/lib/modules/rfkill.ko
    insmod /vendor/lib/modules/cfg80211.ko
    insmod /vendor/lib/modules/iwlwifi.ko
'''
VM_SCRIPT='''#!/usr/bin/env python3
import hashlib,json,pathlib,tarfile
RULES=['init','sysfs','file','open']
GRAPHICS_RULES=[]
RULES += GRAPHICS_RULES
def main():
    overlays={}
    for n in TOOLS:
        pass
    for n,b in overlays.items():
            m=meta(SOURCE,'/'+n,OFFSET);ti=tarfile.TarInfo(n);ti.size=len(b)
'''
GRUB='''menuentry 'x' {
 insmod smbios
 set allowed=0
 if [ "$allowed" = "1" ]; then
  linux /EFI/GBRUNCH/kernel CMDLINE
  initrd /EFI/GBRUNCH/initrd.gz
 fi
}
'''

@unittest.skipUnless(TOOLS,'e2fsprogs required')
class KernelProfileDerivationTests(unittest.TestCase):
    def setUp(self):
        self.t=pathlib.Path(tempfile.mkdtemp(prefix='gbrunch-kernel-test-'));kit=self.t/'kit';(kit/'licenses').mkdir(parents=True);(kit/'firmware').mkdir()
        root=self.t/'root';(root/'firmware').mkdir(parents=True);(root/'init.gbrunch.rc').write_text(INIT_RC)
        subprocess.run(['mke2fs','-q','-t','ext4','-d',str(root),str(kit/'compat.ext4'),'8M'],check=True)
        subprocess.run(['debugfs','-w','-R','ea_set /init.gbrunch.rc security.selinux u:object_r:vendor_configs_file:s0',str(kit/'compat.ext4')],check=True,capture_output=True)
        initrd=ppa.cpio_newc([('payload/native_vendor_in_vm.py',0o100755,VM_SCRIPT.encode()),('payload/modules.json',0o100644,b'[{"file":"xe.ko"}]')])
        (kit/'tool-initrd.gz').write_bytes(gzip.compress(initrd,mtime=0));(kit/'native-grub.cfg').write_text(GRUB)
        files={str(f.relative_to(kit)):sha(f.read_bytes()) for f in kit.rglob('*') if f.is_file()}
        (kit/'native-assets.json').write_text(json.dumps({'scope':'test','cmdline':'rdinit=/init androidboot.init_rc=/oem/init.gbrunch.rc',
            'google_kernel_sha256':'1'*64,'firmware':[],'modules':[{'name':'xe','file':'xe.ko','sha256':'2'*64,'depends':[],'aliases':[]}],'files':files}))
        self.kdir=self.t/'kbuild';self.kdir.mkdir();self.kspec=fake_kernel_build(self.kdir,'ktest')
        self.saved=dict(ppa.PROFILES);ppa.PROFILES.clear()
        ppa.PROFILES['ktest']={'board':'Surface Pro 7','compat_sha256':None,'kernel':True,'stock_modules':False,'evidence':'synthetic','files':[],
            'cmdline_append':'androidboot.gbrunch_kernel=gbrunch-sp7',
            'oem_modules':[{'module':'wifi','stage':'post-fs-data'},{'module':'gpu_drv','stage':'post-fs-data'}],
            'policy_rules':[('shell','hidraw_device','chr_file','open,read,write,ioctl')]}
        self.kit=kit
    def tearDown(self):
        ppa.PROFILES.clear();ppa.PROFILES.update(self.saved);shutil.rmtree(self.t)
    def dump(self,image,path):
        out=self.t/'dump';subprocess.run(['debugfs','-R','dump %s %s'%(path,out),str(image)],check=True,capture_output=True);return out.read_bytes()
    def test_kernel_profile_assets(self):
        out=self.t/'out'
        sys.argv=['x','--assets',str(self.kit),'--output',str(out),'--profile','ktest','--firmware-cache',str(self.t/'cache'),'--kernel',str(self.kdir)];ppa.main()
        m=json.loads((out/'native-assets.json').read_text())
        self.assertEqual(m['replacement_kernel']['release'],'6.18.21-gbrunch-test');self.assertEqual(m['replacement_kernel']['sha256'],sha(b'bzImage-bytes'))
        self.assertEqual((out/'kernel').read_bytes(),b'bzImage-bytes');self.assertEqual(m['google_kernel_sha256'],'1'*64)
        # First-stage modules: the GPU closure from the kernel build, in dependency order, present in the kit.
        self.assertEqual([v['file'] for v in m['modules']],['helper.ko','gpu-drv.ko'])
        self.assertEqual((out/'modules/gpu-drv.ko').read_bytes(),b'gpu-module')
        for n,h in m['files'].items():self.assertEqual(sha((out/n).read_bytes()),h,n)
        # The tool VM extracts no Google modules: the last payload/modules.json in the initrd is empty.
        raw=gzip.decompress((out/'tool-initrd.gz').read_bytes())
        self.assertEqual(ppa.cpio_last(raw,'payload/modules.json'),b'[]\n')
        self.assertEqual(json.loads(ppa.cpio_last(raw,'payload/extra-rules.json')),[['shell','hidraw_device','chr_file','open,read,write,ioctl']])
        vm=ppa.cpio_last(raw,'payload/native_vendor_in_vm.py').decode()
        self.assertIn("extra-rules.json",vm);self.assertIn('RULES+=list(rule)',vm)
        self.assertEqual(json.loads((out/'profile-ktest.json').read_text())['policy_rules'],[['shell','hidraw_device','chr_file','open,read,write,ioctl']])
        rc=self.dump(out/'compat.ext4','/init.gbrunch.rc').decode()
        self.assertNotIn('/vendor/lib/modules',rc);self.assertNotIn('/system/lib/modules',rc)
        self.assertIn('insmod /oem/modules/ac.ko',rc)  # owned lines stay
        early=rc.split('on early-init\n',1)[1].split('\non ',1)[0]
        self.assertIn('    setprop sys.use_memfd true\n',early)  # no /dev/ashmem on a RUST=n kernel
        post=rc.split('on post-fs-data\n',1)[1]
        # wifi needs helper; gpu_drv needs helper too, which is listed once.
        self.assertEqual([l.strip() for l in post.splitlines() if l.strip()],
                         ['insmod /oem/modules/helper.ko','insmod /oem/modules/wifi.ko','insmod /oem/modules/gpu-drv.ko'])
        self.assertEqual(self.dump(out/'compat.ext4','/modules/wifi.ko'),b'wifi-module')
        self.assertIn('androidboot.gbrunch_kernel=gbrunch-sp7',m['cmdline'])
        record=json.loads((out/'profile-ktest.json').read_text())
        self.assertEqual(record['kernel']['release'],'6.18.21-gbrunch-test')
        self.assertFalse((out/'kit.json').exists())  # the synthetic profile has none; real profiles ship theirs
        self.assertEqual([x['file'] for x in record['oem_modules']],['helper.ko','wifi.ko','gpu-drv.ko'])
    def test_local_vendor_file_never_enters_the_kit(self):
        secret=b'microsoft-firmware-bytes'
        ppa.PROFILES['ktest']['vendor_files']=[{'path':'firmware/intel/ipu/fw.bin','local':'intel/ipu/fw.bin','bytes':len(secret),
            'sha256':hashlib.sha256(secret).hexdigest(),'template':'/firmware/x.bin','fetch':'fetch it'}]
        ppa.PROFILES['ktest']['oem_placeholders']=['firmware/intel/ipu/fw.bin']
        out=self.t/'o4'
        sys.argv=['x','--assets',str(self.kit),'--output',str(out),'--profile','ktest','--firmware-cache',str(self.t/'cache'),'--kernel',str(self.kdir)];ppa.main()
        m=json.loads((out/'native-assets.json').read_text())
        self.assertEqual(m['local_files'],[{'local':'intel/ipu/fw.bin','sha256':hashlib.sha256(secret).hexdigest(),'bytes':len(secret),'fetch':'fetch it'}])
        extra=json.loads(ppa.cpio_last(gzip.decompress((out/'tool-initrd.gz').read_bytes()),'payload/extra-vendor.json'))
        self.assertEqual(extra,[{'path':'firmware/intel/ipu/fw.bin','template':'/firmware/x.bin','local':'intel/ipu/fw.bin','sha256':hashlib.sha256(secret).hexdigest()}])
        for f in out.rglob('*'):
            if f.is_file():self.assertNotIn(secret,f.read_bytes() if f.suffix!='.gz' else gzip.decompress(f.read_bytes()),f)
        self.assertEqual(self.dump(out/'compat.ext4','/firmware/intel/ipu/fw.bin'),b'')  # placeholder only
        vm=ppa.cpio_last(gzip.decompress((out/'tool-initrd.gz').read_bytes()),'payload/native_vendor_in_vm.py').decode()
        self.assertIn("/payload/local",vm);self.assertIn('LOCAL_FILE_MISSING',vm)
    def test_bad_policy_rule_refused(self):
        ppa.PROFILES['ktest']['policy_rules']=[('shell','hidraw_device','chr_file','open; reboot')]
        sys.argv=['x','--assets',str(self.kit),'--output',str(self.t/'o3'),'--profile','ktest','--firmware-cache',str(self.t/'cache'),'--kernel',str(self.kdir)]
        with self.assertRaises(ValueError):ppa.main()
        self.assertFalse((self.t/'o3').exists())
    def test_kernel_profile_requires_kernel_dir_and_matching_profile(self):
        sys.argv=['x','--assets',str(self.kit),'--output',str(self.t/'o1'),'--profile','ktest','--firmware-cache',str(self.t/'cache')]
        with self.assertRaises(SystemExit):ppa.main()
        other=self.t/'other';other.mkdir();fake_kernel_build(other,'someone-else')
        sys.argv=['x','--assets',str(self.kit),'--output',str(self.t/'o2'),'--profile','ktest','--firmware-cache',str(self.t/'cache'),'--kernel',str(other)]
        with self.assertRaises(ValueError):ppa.main()
        self.assertFalse((self.t/'o2').exists())

if __name__=='__main__':unittest.main()
