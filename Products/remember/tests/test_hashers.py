import unittest

from zope.app.annotation.interfaces import IAnnotations
from zope.component import getAdapter

from Products.CMFCore.utils import getToolByName
from Products.remember.interfaces import IHashPW
from Products.remember.config import HASHERS
from Products.remember.config import ANNOT_KEY

from test_project import RememberProjectTest
from test_project import pmem_values

class TestHasher(RememberProjectTest):
    """ 
    test the different hashing methods available
    """

    def test_hashers(self):
        for htype in HASHERS:
            login_id = 'hashtest_%s' % htype
            member = self.addMember(login_id)
            if not getAdapter(member, IHashPW, htype).isAvailable(): continue
            
            mbtool = getToolByName(member, 'membrane_tool')
            annot = IAnnotations(mbtool)
            annot.setdefault(ANNOT_KEY, {})['hash_type'] = htype
            member.setRoles('Member')
            member.processForm(values=pmem_values)

            password = member.getPassword()
            hash_type, hashed = password.split(':', 1)

            self.assertEqual(htype, hash_type)
            self.failUnless(member.verifyCredentials(dict(login=login_id,
                                                          **pmem_values)))

def test_suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(TestHasher))
    return suite