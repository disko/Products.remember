import os, sys
import unittest

from base import RememberTestBase
from base import ZTCLayer

from zope.app.annotation.interfaces import IAnnotations

from zope.component import getMultiAdapter

from Products.Archetypes.tests.ArchetypesTestCase import ArcheSiteTestCase

from Products.CMFCore.utils import getToolByName
from Products.CMFPlone.interfaces import IPloneSiteRoot

from Products.GenericSetup.testing import DummySetupEnviron
from Products.GenericSetup import profile_registry, EXTENSION

from Products.CMFFormController.ControllerState import ControllerState

from Products.membrane.interfaces import ICategoryMapper
from Products.membrane.config import ACTIVE_STATUS_CATEGORY
from Products.membrane.utils import generateCategorySetIdForType

from Products.remember.config import DEFAULT_MEMBER_TYPE
from Products.remember.config import ANNOT_KEY
from Products.remember.exportimport.membranetool import \
     RememberMembraneToolXMLAdapter

from Products.remember.tools.memberdata import MemberDataContainer

from Products.GenericSetup.context import BaseContext
from DateTime.DateTime import DateTime

# set up XML profile for testing
profile_registry.registerProfile('test',
                                 'remember',
                                 'Testing extension profile for membrane',
                                 'profiles/test',
                                 'remember',
                                 EXTENSION,
                                 for_=IPloneSiteRoot)


class TestRememberProfiles(ArcheSiteTestCase):
    """
    Uses a different layer so the profile import can be tested.
    """
    layer = ZTCLayer

    def testLayerWorks(self):
        """ verify members have not been added yet """
        ttool = self.portal.portal_types
        self.failIf('Member' in ttool.listContentTypes())

    def testImportHash(self):
        """ 
        test importing the hash from XML
        annotates membrane_tool
        """
        
        app = self.app
        setup_tool = app.plone.portal_setup

        # ensure that membrane_tool does not yet exist
        self.assertRaises(AttributeError, getattr,
                          self.portal, 'membrane_tool')

        # the membrane tool needs to be imported first
        setup_tool.setImportContext('profile-membrane:default')
        setup_tool.runAllImportSteps()

        # imports the xml file in the test profile
        setup_tool.setImportContext('profile-remember:test')
        setup_tool.runAllImportSteps()

        mbtool = self.portal.membrane_tool
        annot = IAnnotations(mbtool)
       
        self.failUnless(ANNOT_KEY in annot)
        self.assertEqual('bcrypt', annot[ANNOT_KEY]['hash_type'])

class TestRememberInstall(RememberTestBase):

    def testActiveWFStates(self):
        cat_set = generateCategorySetIdForType(DEFAULT_MEMBER_TYPE)
        cat_map = ICategoryMapper(self.mbtool)
        states = cat_map.listCategoryValues(cat_set,
                                            ACTIVE_STATUS_CATEGORY)
        self.failUnless('private' in states and 'public' in states)

    def testPreferencesURL(self):
        self.login('test_user_1_')
        prefs_url = self.portal.restrictedTraverse('prefs_url')()
        self.assertEqual(prefs_url,
                         '%s/personalize_form' % self.portal.absolute_url())
        mem = self.portal_member
        self.login('portal_member')
        prefs_url = self.portal.restrictedTraverse('prefs_url')()
        self.assertEqual(prefs_url,
                         '%s/edit' % mem.absolute_url())


class TestRememberMembraneToolXMLAdapter(RememberTestBase):
    """test to see if the XML file is read correctly for remember"""
    
    def afterSetUp(self):
        RememberTestBase.afterSetUp(self)

        self.dummy_env = DummySetupEnviron()
        self.adapter = getMultiAdapter((self.portal.membrane_tool,
                                        self.dummy_env))

    def testAnnotateHash(self):
        """hash-type nodes with valid/invalid data annotate appropriately"""
        class Child(object):
            nodeName = 'hash-type'
            def __init__(self, htype):
                self.htype = htype
            def getAttribute(self, a):
                return self.htype

        class Node(object):
            def __init__(self, htype='bcrypt'):
                self.childNodes = [Child(htype)]

        mbtool = self.portal.membrane_tool
        annot = IAnnotations(mbtool)

        if ANNOT_KEY in annot:
            del annot[ANNOT_KEY]
        self.adapter._annotateHash(Node())
        self.failUnless(ANNOT_KEY in annot)
        self.assertEqual('bcrypt', annot[ANNOT_KEY]['hash_type'])
        del annot[ANNOT_KEY]

        # now test that adding an invalid hash raises a ValueError
        self.assertRaises(ValueError,
                          self.adapter._annotateHash,
                          Node('bogus_type'))

        # verify that not returning any child nodes does not annotate
        n = Node()
        n.childNodes = []
        self.adapter._annotateHash(n)
        annot = IAnnotations(mbtool)
        self.failIf(ANNOT_KEY in annot)

    def testExportNodeNoAnnotation(self):
        """
        when membrane tool is not annotated, hash-type node should not
        get exported
        """
        node = self.adapter._exportNode()
        self.failIf('<hash-type' in node.toxml())

    def testExportNodeWithAnnotation(self):
        """
        when membrane tool is annotated, hash-type node should get exported
        attribute 'name' on the node should contain the hash-type
        """
        # initially add the annotation on to the membrane_tool
        annot = IAnnotations(self.portal.membrane_tool)
        annot.setdefault(ANNOT_KEY, {})['hash_type'] = 'bcrypt'
        node = self.adapter._exportNode()
        self.failUnless('<hash-type name="bcrypt"/>' in node.toxml())
        # clear the bogus annotation
        del annot[ANNOT_KEY]

class TestCMFFormControllerAction(RememberTestBase):
    """
    an action should be added on the CMFFormController that contains a redirect
    to the edit page after saving a member's preferences
    """
    
    def testAdditionalActionImported(self):
        """
        additional traverse to action should exist on the cmf form controller
        """
        cf = getToolByName(self.portal, 'portal_form_controller')
        actions = cf.listFormActions()
        self.failUnless(actions)

        for action in actions:
            arg = action.getActionArg()
            if arg == 'string:choose_destination':
                break
        else:
            self.fail('no choose_destination traversal action found')

    def testForEditAction(self):
        """
        verify that an edit page is returned after a member edit
        has been validated
        """
        mem = self.portal_member
        state = ControllerState(id=mem.id, context=mem, button=None,
                                status='success', next_action=None)
        mem.REQUEST.set('controller_state', state)

        page_template = self.portal.portal_skins.remember.base_edit
        nextPage = page_template.getNext(state, mem.REQUEST)
        self.failUnless('<form name="edit_form"' in nextPage)
        self.failUnless('Changes saved.' in nextPage)


class TestSearchIndicesInstalled(RememberTestBase):
    """
    verify that the search indices for the membrane tool are installed
    """
    def testIndicesExist(self):
        """
        check the list of indices are present in membrane tool search indices
        """
        membrane_idxs = self.mbtool.indexes()
        remember_idxs = \
            'getId getFullname getEmail getRoles getGroups getReview_state'
        remember_idxs = remember_idxs.split()
        for idx in remember_idxs:
            self.failUnless(idx in membrane_idxs)

 
def test_suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(TestRememberInstall))
    suite.addTest(unittest.makeSuite(TestRememberMembraneToolXMLAdapter))
    suite.addTest(unittest.makeSuite(TestRememberProfiles))
    suite.addTest(unittest.makeSuite(TestCMFFormControllerAction))
    suite.addTest(unittest.makeSuite(TestSearchIndicesInstalled))
    return suite
