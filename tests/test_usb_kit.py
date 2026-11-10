#!/usr/bin/env python3
"""USB kit templates: every profile kit.json renders both READMEs without leftover placeholders."""
import json,pathlib,subprocess,sys,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools/usb'))
import kit_profile

class KitTemplateTests(unittest.TestCase):
    def test_every_profile_renders(self):
        kits=sorted(ROOT.glob('profiles/*/kit.json'))
        self.assertTrue(kits)
        for kit in kits:
            k=kit_profile.load(kit.parent)
            self.assertEqual(k['profile'],kit.parent.name)
            for tpl in ['README-usb-kit.txt','README.txt']:
                out=kit_profile.render(k,(ROOT/'tools/usb/kit'/tpl).read_text())
                self.assertNotIn('@',out,tpl)
            self.assertIn(k['image'],kit_profile.render(k,(ROOT/'tools/usb/kit/README-usb-kit.txt').read_text()))
            v=kit_profile.values(k)
            self.assertTrue(v['VENDOR_EROFS'].startswith('gbvendor-') and v['VENDOR_EROFS'].endswith('.erofs'))
            if k['kernel']:self.assertIn('apply-image-update.sh',v['KERNEL_UPDATE'])
    def test_scripts_parse(self):
        for s in ROOT.glob('tools/usb/*.sh'):
            subprocess.run(['bash','-n',str(s)],check=True)
    def test_image_names_are_distinct(self):
        kits=list(ROOT.glob('profiles/*/kit.json'))
        self.assertEqual(len({json.loads(k.read_text())['image'] for k in kits}),len(kits))

if __name__=='__main__':unittest.main()
