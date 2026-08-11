import os, json, tempfile, unittest
from contact_manager import ContactManager, validate_phone

class T(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mktemp(suffix='.json')
        self.m = ContactManager(self.t)
    def tearDown(self):
        if os.path.exists(self.t): os.remove(self.t)
    def test_vp_v(self):
        self.assertTrue(validate_phone('555-1'))
    def test_vp_i(self):
        self.assertFalse(validate_phone(''))
        self.assertFalse(validate_phone('abc'))
        self.assertFalse(validate_phone(None))
    def test_add(self):
        c = self.m.add_contact('Alice','555-1','a@e.com')
        self.assertEqual(c['name'],'Alice'); self.assertEqual(c['id'],1)
    def test_add_inc(self):
        self.assertEqual(self.m.add_contact('A','555-1')['id'],1)
        self.assertEqual(self.m.add_contact('B','555-2')['id'],2)
    def test_add_email(self):
        self.assertEqual(self.m.add_contact('A','555-1')['email'],'')
    def test_add_no_name(self):
        with self.assertRaises(ValueError): self.m.add_contact('','555-1')
    def test_add_bad_phone(self):
        with self.assertRaises(ValueError): self.m.add_contact('A','')
        with self.assertRaises(ValueError): self.m.add_contact('A','xx')
    def test_add_dup(self):
        self.m.add_contact('A','555-1')
        with self.assertRaises(ValueError): self.m.add_contact('B','555-1')
    def test_add_strip(self):
        c = self.m.add_contact('  A  ','  555-1  ','  a@e.com  ')
        self.assertEqual(c['name'],'A'); self.assertEqual(c['phone'],'555-1')
    def test_get_ok(self):
        c = self.m.add_contact('A','555-1')
        self.assertEqual(self.m.get_contact(c['id'])['name'],'A')
    def test_get_no(self):
        self.assertIsNone(self.m.get_contact(999))
    def test_list_e(self):
        self.assertEqual(self.m.list_contacts(),[])
    def test_list_m(self):
        self.m.add_contact('A','555-1'); self.m.add_contact('B','555-2')
        self.assertEqual(len(self.m.list_contacts()),2)
    def test_edit_n(self):
        c = self.m.add_contact('A','555-1')
        self.assertEqual(self.m.edit_contact(c['id'],name='B')['name'],'B')
    def test_edit_p(self):
        c = self.m.add_contact('A','555-1')
        self.assertEqual(self.m.edit_contact(c['id'],phone='555-9')['phone'],'555-9')
    def test_edit_dup(self):
        self.m.add_contact('A','555-1'); c2=self.m.add_contact('B','555-2')
        with self.assertRaises(ValueError): self.m.edit_contact(c2['id'],phone='555-1')
    def test_edit_no(self):
        with self.assertRaises(KeyError): self.m.edit_contact(999,name='X')
    def test_edit_bf(self):
        c = self.m.add_contact('A','555-1')
        with self.assertRaises(ValueError): self.m.edit_contact(c['id'],id=5)
    def test_edit_bp(self):
        c = self.m.add_contact('A','555-1')
        with self.assertRaises(ValueError): self.m.edit_contact(c['id'],phone='xx')
    def test_edit_bn(self):
        c = self.m.add_contact('A','555-1')
        with self.assertRaises(ValueError): self.m.edit_contact(c['id'],name='')
    def test_edit_noop(self):
        c = self.m.add_contact('A','555-1')
        self.assertEqual(self.m.edit_contact(c['id'])['name'],'A')
    def test_del(self):
        c = self.m.add_contact('A','555-1')
        self.assertTrue(self.m.delete_contact(c['id']))
        self.assertIsNone(self.m.get_contact(c['id']))
    def test_del_no(self):
        with self.assertRaises(KeyError): self.m.delete_contact(999)
    def test_del_id(self):
        self.m.add_contact('A','555-1'); self.m.add_contact('B','555-2')
        self.m.delete_contact(1)
        self.assertEqual(self.m.add_contact('C','555-3')['id'],3)
    def test_s_n(self):
        self.m.add_contact('Alice','555-1'); self.m.add_contact('Bob','555-2')
        self.assertEqual(len(self.m.search_contacts('Alice')),1)
    def test_s_ci(self):
        self.m.add_contact('ALICE','555-1')
        self.assertEqual(len(self.m.search_contacts('alice')),1)
    def test_s_p(self):
        self.m.add_contact('A','555-1234'); self.m.add_contact('B','555-5678')
        self.assertEqual(len(self.m.search_contacts('1234')),1)
    def test_s_nr(self):
        self.m.add_contact('A','555-1')
        self.assertEqual(self.m.search_contacts('zzz'),[])
    def test_s_nk(self):
        self.m.add_contact('A','555-1'); self.m.add_contact('B','555-2')
        self.assertEqual(len(self.m.search_contacts()),2)
    def test_per_r(self):
        c = self.m.add_contact('Alice','555-1','a@e.com')
        self.m.edit_contact(c['id'],name='Alice S')
        self.assertEqual(ContactManager(self.t).get_contact(c['id'])['name'],'Alice S')
    def test_per_f(self):
        self.m.add_contact('A','555-1')
        self.assertTrue(os.path.exists(self.t))
    def test_per_d(self):
        self.m.add_contact('A','555-1'); c2=self.m.add_contact('B','555-2')
        self.m.delete_contact(c2['id'])
        m2=ContactManager(self.t)
        self.assertEqual(len(m2.list_contacts()),1)
        self.assertIsNone(m2.get_contact(2))

if __name__=='__main__': unittest.main()