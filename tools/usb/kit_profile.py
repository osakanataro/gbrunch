#!/usr/bin/env python3
"""Profile facts for the USB kits: read assets-*/kit.json and render the kit README templates.

  kit_profile.py ASSETS get KEY                 print one value (image, profile, device, ...)
  kit_profile.py ASSETS render TEMPLATE OUT      fill @PLACEHOLDERS@ of a kit/*.txt template
  kit_profile.py ASSETS check TEMPLATE...        exit 1 if a template keeps an unknown placeholder

kit.json is profiles/<profile>/kit.json, copied into the asset directory by tools/prepare_profile_assets.py:
  profile, device        names;  image: the disk image file name (kept stable per profile)
  firmware_setup, boot_menu, secure_boot   how to reach the firmware, the boot device menu, and turn
                                            Secure Boot off on that laptop
  kernel                 true for a replacement-kernel profile (kernel updates need apply-image-update.sh)
  notes, status          lists of lines shown in the README
Placeholders: @IMAGE@ @PROFILE@ @DEVICE@ @FIRMWARE_SETUP@ @BOOT_MENU@ @SECURE_BOOT@ @VENDOR_EROFS@
@NOTES@ @STATUS@ @KERNEL_UPDATE@ (indented list / paragraph placeholders may be empty).
"""
import json,pathlib,re,sys

KEYS=['profile','device','image','firmware_setup','boot_menu','secure_boot']

def load(assets):
    k=json.loads((pathlib.Path(assets)/'kit.json').read_text())
    for key in KEYS:
        if not isinstance(k.get(key),str) or not k[key]:raise SystemExit('kit.json lacks '+key)
    if not re.fullmatch(r'[A-Za-z0-9._-]+\.img',k['image']):raise SystemExit('kit.json image must be a plain .img file name')
    k.setdefault('notes',[]);k.setdefault('status',[]);k.setdefault('kernel',False)
    return k

def values(k):
    lines=lambda xs,ind:''.join(ind+x+'\n' for x in xs).rstrip('\n')
    vendor=k['image'][:-4].replace('googlebook-','gbvendor-')+'.erofs'
    kernel_update=('A NEW KERNEL (replacement-kernel profile) lives in the EFI partition and the initrd: apply it with\n'
                   '     sudo bash usb/apply-image-update.sh /dev/sdX '+k['image']+'   (copies EFI-SYSTEM, GBRUNCH and GBVENDOR; keeps userdata)'
                   if k['kernel'] else
                   'This profile boots Google\'s own kernel; apply-profile-update.sh and apply-vendor-update.sh cover every update.')
    return {'IMAGE':k['image'],'PROFILE':k['profile'],'DEVICE':k['device'],'FIRMWARE_SETUP':k['firmware_setup'],
            'BOOT_MENU':k['boot_menu'],'SECURE_BOOT':k['secure_boot'],'VENDOR_EROFS':vendor,
            'NOTES':lines(k['notes'],'   '),'STATUS':lines(k['status'],'  '),'KERNEL_UPDATE':kernel_update}

def render(k,template):
    v=values(k)
    out=re.sub(r'@([A-Z_]+)@',lambda m:v[m.group(1)] if m.group(1) in v else m.group(0),template)
    left=re.findall(r'@[A-Z_]+@',out)
    if left:raise SystemExit('unknown placeholders: '+' '.join(sorted(set(left))))
    return re.sub(r'\n{3,}','\n\n',out)

def main():
    if len(sys.argv)<3:raise SystemExit(__doc__)
    k=load(sys.argv[1]);cmd=sys.argv[2]
    if cmd=='get':print(k[sys.argv[3]] if sys.argv[3]!='vendor_erofs' else values(k)['VENDOR_EROFS'])
    elif cmd=='render':pathlib.Path(sys.argv[4]).write_text(render(k,pathlib.Path(sys.argv[3]).read_text()))
    elif cmd=='check':
        for t in sys.argv[3:]:render(k,pathlib.Path(t).read_text())
    else:raise SystemExit(__doc__)

if __name__=='__main__':main()
