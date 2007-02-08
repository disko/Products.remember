from AccessControl import ClassSecurityInfo
from AccessControl import Unauthorized
from AccessControl.PermissionRole import rolesForPermissionOn
from Globals import InitializeClass
from Acquisition import aq_base

from zope.interface import implements
from zope.app.annotation.interfaces import IAttributeAnnotatable
from zope.app.annotation.interfaces import IAnnotations
from zope.component import getAdapter

from Products.CMFCore.utils import getToolByName
from Products.Archetypes import public as atapi
from Products.CMFDynamicViewFTI.browserdefault import BrowserDefaultMixin

from Products.CMFCore import permissions as cmfpermissions
from Products.CMFCore.utils import _checkPermission

from Products.PluggableAuthService.interfaces.authservice import IPluggableAuthService
from Products.PluggableAuthService.interfaces.plugins import IRoleAssignerPlugin

from Products.PlonePAS.interfaces.plugins import IUserManagement
from Products.PlonePAS.interfaces.group import IGroupManagement
from Products.PlonePAS.interfaces.propertysheets import IMutablePropertySheet
from Products.PlonePAS.interfaces.capabilities import IDeleteCapability, IPasswordSetCapability
from Products.PlonePAS.interfaces.capabilities import IGroupCapability, IAssignRoleCapability
from Products.PlonePAS.interfaces.capabilities import IManageCapabilities

from Products.membrane.interfaces import IPropertiesProvider
from Products.membrane.interfaces import IGroupAwareRolesProvider
from Products.membrane.interfaces import IUserRoles
from Products.membrane.interfaces import IUserAuthentication
from Products.membrane.interfaces import IUserDeleter
from Products.membrane.interfaces import IMembraneUserDeleter

from Products.remember.interfaces import IReMember
from Products.remember.interfaces import IRememberAuthProvider
from Products.remember.interfaces import IRememberGroupsProvider
from Products.remember.interfaces import IHashPW
from Products.remember.interfaces import IRememberUserChanger

from Products.remember.config import ALLOWED_MEMBER_ID_PATTERN
from Products.remember.config import DEFAULT_MEMBER_TYPE
from Products.remember.config import ANNOT_KEY
from Products.remember.config import HASHERS
from Products.remember.utils import stringToList
from Products.remember.utils import removeAutoRoles
from Products.remember.permissions import EDIT_PROPERTIES_PERMISSION
from Products.remember.permissions import VIEW_PUBLIC_PERMISSION
from Products.remember.Extensions.workflow import triggerAutomaticTransitions

from member_schema import content_schema
metadata_schema = atapi.ExtensibleMetadata.schema.copy()

import logging
logger = logging.getLogger('remember')

_marker = []

# Establish the field order
member_schema = content_schema + metadata_schema
# metadata_schema doesn't override any fields in content_schema
member_schema = member_schema + content_schema

class BaseMember(object):
    """
    Abstract member object base class.
    """
    security = ClassSecurityInfo()

    implements(IReMember, IRememberAuthProvider, IUserAuthentication,
               IPropertiesProvider, IRememberGroupsProvider,
               IGroupAwareRolesProvider, IUserRoles,
               IManageCapabilities, IAttributeAnnotatable,
               IRememberUserChanger, IUserDeleter)

    archetype_name = portal_type = meta_type = DEFAULT_MEMBER_TYPE
    base_archetype = None

    # Give a nice icon
    content_icon = "user.gif"
    
    # Note that we override BaseContent.schema
    schema = member_schema
    
    global_allow = 0

    # for Plone compatibility -- managed by workflow state
    listed = 0

    default_roles = ('Member',)

    security.declarePrivate('setId')
    def setId(self, value):
        """
        Have to fix up the ownership when the id changes.
        """
        self.base_archetype.setId(self, value)
        self.fixOwnership()

    security.declarePrivate('fixOwnership')
    def fixOwnership(self, old_id=None):
        """
        Member objects should always be owned by the corresponding
        user, if one exists.
        """
        roles = tuple()
        if old_id is not None:
            roles = self.get_local_roles_for_userid(old_id)
            self.manage_delLocalRoles([old_id])
        user = self.getUser()
        if user is not None:
            self.changeOwnership(user, 1)
            if 'Owner' not in roles:
                roles += ('Owner',)
            self.manage_setLocalRoles(user.getId(), roles)

    security.declareProtected(VIEW_PUBLIC_PERMISSION, 'hasUser')
    def hasUser(self):
        uf = getToolByName(self, 'acl_users')
        if uf.getUser(self.getId()) is not None:
            return True

    security.declarePrivate('getUser')
    def getUser(self):
        uf = getToolByName(self, 'acl_users')
        user = uf.getUserById(self.getId())
        return user.__of__(self)

    security.declarePrivate('getDefaultRoles')
    def getDefaultRoles(self):
        return self.default_roles

    security.declareProtected(VIEW_PUBLIC_PERMISSION, 'fileAs')
    def fileAs(self):
        """
        Returns a user friendly identifier of the member, fullname by
        default.  can be overridden in subclasses to support different
        filing policies.  Used by the title field.
        """
        return self.getFullname()

    #######################################################################
    # Validators and vocabulary methods
    #######################################################################
    security.declarePrivate('validate_id')
    def validate_id(self, id):
        # we can't always trust the id argument, b/c the autogen'd
        # id will be passed in if the reg form id field is blank
        form = self.REQUEST.form
        if form.has_key('id') and not form['id']:
            return self.translate('Input is required but no input given.',
                                  default='You did not enter a login name.'),
        elif self.id and id != self.id:
            # we only validate if we're changing the id
            mbtool = getToolByName(self, 'membrane_tool')
            if mbtool.getUserAuthProvider(id) is not None or \
                   not ALLOWED_MEMBER_ID_PATTERN.match(id) or \
                   id == 'Anonymous User':
                msg = "The login name you selected is already " + \
                      "in use or is not valid. Please choose another."
                return self.translate(msg, default=msg)

    security.declarePrivate('validate_password')
    def validate_password(self, password):
        # no change -- ignore
        if not password:
            return None
        regtool = getToolByName(self, 'portal_registration')
        return regtool.testPasswordValidity(password)

    security.declarePrivate('validate_roles')
    def validate_roles(self, roles):
        roles = stringToList(roles)
        valid = self.valid_roles()

        for r in roles:
            if r not in valid:
                return '%s is not a valid role.' % (r)
        return None

    security.declarePrivate('post_validate')
    def post_validate(self, REQUEST, errors):
        form = REQUEST.form
        if form.has_key('password'):
            password = form.get('password', None)
            confirm = form.get('confirm_password', None)
            
            # test to see if we are on the reg_form so we don't have
            # to enter a password on the base_edit form
            is_reg_form = int(form.get('is_reg_form', 0))
            if is_reg_form and not password:
                errors['password'] = \
                    self.translate('Input is required but no input given.',
                                   default='You did not enter a password.')

            if not errors.get('password', None):
                if password and \
                       (password == REQUEST.get('id', None) or \
                        password == self.id):
                    errors['password'] = \
                        self.translate('id_pass_same',
                                       default="Your username and password are the " +
                                       "same.  This is really not a good idea.",
                                       domain='remember-plone'),
                    
            if not (errors.get('password', None)) and \
                   not (errors.get('confirm_password', None)):
                if password != confirm:
                    errors['password'] = \
                        errors['confirm_password'] = \
                        self.translate('Passwords do not match.',
                                       default='Passwords do not match.'),

    security.declarePublic('isValid')
    def isValid(self):
        """
        Check to make sure a Member object's fields satisfy schema
        constraints
        """
        errors = {}
        # make sure object has required data and metadata
        self.Schema().validate(self, None, errors, 1, 1)
        if errors:
            return 0
        return 1

    # Vocabulary methods
    def editors(self):
        ptool = getToolByName(self, 'portal_properties')
        return ptool.site_properties.available_editors

    def filtered_valid_roles(self):
        """ return valid roles minus any automatic roles """
        roles = list(self.valid_roles())
        removeAutoRoles(roles)
        return tuple(roles)

    def valid_groups(self):
        """ return the set of valid groups """
        gtool = getToolByName(self, 'portal_groups')
        return gtool.getGroupIds()

    def available_skins(self):
        # give managers the ability to choose any skin
        mtool = getToolByName(self, 'portal_membership')
        managePortal = mtool.checkPermission(cmfpermissions.ManagePortal,
                                             self)
        skins_tool = getToolByName(self, 'portal_skins')
        if skins_tool.getAllowAny() or managePortal:
            return getToolByName(self, 'portal_skins').getSkinSelections()
        else:
            return [self.getPortalSkin()]

    def getDefaultSkin(self):
        return getToolByName(self, 'portal_skins').getDefaultSkin()

    def getSiteLanguages(self):
        return atapi.DisplayList(self.availableLanguages())

    #######################################################################
    # Contract with portal_membership
    #######################################################################
    security.declarePublic('getMemberId')
    def getMemberId(self):
        """Get the member id """
        return self.getUserName()

    security.declareProtected(EDIT_PROPERTIES_PERMISSION, 'setProperties')
    def setProperties(self, mapping=None, **kwargs):
        """
        assign all the props to member attributes, we expect to be
        able to find a mutator for each of these props
        """
        # if mapping is not a dict, assume it is REQUEST
        if mapping:
            if not type(mapping) == type({}):
                data = {}
                for k,v in mapping.form.items():
                    data[k] = v
                mapping = data
        else:
            mapping = {}

        if kwargs:
            # mapping could be a request object thats not really a dict,
            # this is what we get
            mapping.update(kwargs)

        for fieldname in mapping.keys():
            # have to check permissions by hand... ugh!
            field = self.getField(fieldname)
            if field is not None and not field.checkPermission("edit", self):
                raise Unauthorized
                        
        self.update(**mapping)

    security.declarePrivate('setMemberProperties')
    def setMemberProperties(self, mapping):
        self.setProperties(mapping)

    def _getProperty(self, id):
        """Try to get a member property.  If the property is not found,
        raise an AttributeError"""
        field = self.Schema().get(id, None)
        if field is not None:
            if not field.checkPermission('view', self):
                raise Unauthorized
            accessor = getattr(self, field.accessor, None)
            value = accessor()
        else:
            base = aq_base(self)
            value = getattr(base, id)
        return value

    security.declarePublic('getProperty')
    def getProperty(self, id, default=_marker):
        try:
            return self._getProperty(id)
        except AttributeError:
            # member does not have a value for given property
            # try memberdata_tool for default value
            mdtool = getToolByName(self, 'portal_memberdata')
            tool_value = mdtool.getProperty(id, _marker)
            user_value = getattr(self.getUser(), id, _marker)

            # If the tool doesn't have the property, use user_value or default
            if tool_value is _marker:
                if user_value is not _marker:
                    return user_value
                elif default is not _marker:
                    return default
                else:
                    raise ValueError, 'The property %s does not exist' % id

            # If the tool has an empty property and we have a
            # user_value, use it
            if not tool_value and user_value is not _marker:
                return user_value

            # Otherwise return the tool value
            return tool_value

    security.declarePublic('showPasswordField')
    def showPasswordField(self):
        """Indicates if the password fields should be visible on
           either the reg_form or base_edit
        """
        if self.hasUser():
            return False
        site_props = self.portal_properties.site_properties
        return not site_props.validate_email

    # dummy method
    def _setConfirmPassword(self, value):
        pass

    # dummy method
    def _getConfirmPassword(self):
        return ''

    def _getHashType(self):
        """
        Return the hash type to use
        """
        mbtool = getToolByName(self, 'membrane_tool')
        annot = IAnnotations(mbtool)
        try:
            return annot[ANNOT_KEY]['hash_type']
        except KeyError:
            for hash_type in HASHERS:
                hasher = getAdapter(self, IHashPW, hash_type)
                if hasher.isAvailable():
                    return hash_type
        return None

    def _setPassword(self, password):
        if password:
            hash_type = self._getHashType()
            if hash_type is None:
                raise ValueError('Invalid hash_type: None')
            hasher = getAdapter(self, IHashPW, hash_type)
            hashed = hasher.hashPassword(password)
            hash_type_with_password = hash_type + ':' + hashed
            self.getField('password').set(self, hash_type_with_password)
            mtool = getToolByName(self, 'portal_membership')
            # Reset the credentials if the current member initiates
            mem = mtool.getAuthenticatedMember()
            if mem.getUserName() == self.getUserName():
                mtool.credentialsChanged(password)

    #######################################################################
    # IUserAuthentication implementation
    #######################################################################
    def getUserName(self):
        return self.getId()

    def verifyCredentials(self, credentials):
        login = credentials.get('login')
        password = credentials.get('password')
        try:
            hash_type, hashed = self.getPassword().split(':', 1)
        except ValueError:
            raise ValueError('Error parsing hash type. '
                             'Please run migration')
        hasher = getAdapter(self, IHashPW, hash_type)
        if login == self.getUserName() and \
               hasher.validate(hashed, password):
            return True
        else:
            return False


    #######################################################################
    # IManageCapabilities implementation
    #######################################################################
    def canDelete(self):
        """True iff user can be removed from the Plone UI."""
        # IUserManagement provides doDeleteUser
        plugins = self._getPlugins()
        managers = plugins.listPlugins(IUserManagement)
        if managers:
            for mid, manager in managers:
                if IDeleteCapability.providedBy(manager) and \
                   manager.allowDeletePrincipal(self.getId()):
                    return 1
        return 0


    def canPasswordSet(self):
        """True iff user can change password."""
        # IUserManagement provides doChangeUser
        plugins = self._getPlugins()
        managers = plugins.listPlugins(IUserManagement)
        if managers:
            for mid, manager in managers:
                if IPasswordSetCapability.providedBy(manager) and \
                   manager.allowPasswordSet(self.getId()):
                    return 1
        return 0


    def passwordInClear(self):
        """True iff password can be retrieved in the clear (not hashed.)

        False for PAS. It provides no API for getting passwords,
        though it would be possible to add one in the future.
        """
        return 0

    def _memberdataHasProperty(self, prop_name):
        mdata = getToolByName(self, 'portal_memberdata', None)
        if mdata:
            return mdata.hasProperty(prop_name)
        return 0


    def canWriteProperty(self, prop_name):
        """True iff the member/group property named in 'prop_name'
        can be changed.
        """
        if not IPluggableAuthService.providedBy(self.acl_users):
            # not PAS; Memberdata is writable
            return self._memberdataHasProperty(prop_name)
        else:
            # it's PAS
            user = self.getUser()
            sheets = getattr(user, 'getOrderedPropertySheets', lambda: None)()
            if not sheets:
                return self._memberdataHasProperty(prop_name)

            for sheet in sheets:
                if not sheet.hasProperty(prop_name):
                    continue
                if IMutablePropertySheet.providedBy(sheet):
                    return 1
                else:
                    break  # shadowed by read-only
        return 0


    def canAddToGroup(self, group_id):
        """True iff member can be added to group."""
        # IGroupManagement provides IGroupCapability
        plugins = self._getPlugins()
        managers = plugins.listPlugins(IGroupManagement)
        if managers:
            for mid, manager in managers:
                if IGroupCapability.providedBy(manager):
                    return manager.allowGroupAdd(self.getId(), group_id)
        return 0

    def canRemoveFromGroup(self, group_id):
        """True iff member can be removed from group."""
        # IGroupManagement provides IGroupCapability
        plugins = self._getPlugins()
        managers = plugins.listPlugins(IGroupManagement)
        if managers:
            for mid, manager in managers:
                if IGroupCapability.providedBy(manager):
                    return manager.allowGroupRemove(self.getId(), group_id)
        return 0


    def canAssignRole(self, role_id):
        """True iff member can be assigned role. Role id is string."""
        # IRoleAssignerPlugin provides IAssignRoleCapability
        plugins = self._getPlugins()
        managers = plugins.listPlugins(IRoleAssignerPlugin)
        if managers:
            for mid, manager in managers:
                if IAssignRoleCapability.providedBy(manager):
                    return manager.allowRoleAssign(self.getId(), role_id)
        return 0

    ## plugin getters

    security.declarePrivate('_getPlugins')
    def _getPlugins(self):
        return self.acl_users.plugins


    #######################################################################
    # Overrides of base class mutators that trigger workflow transitions
    #######################################################################
    def update(self, **kwargs):
        # XXX Need to remove this once we have real events
        ret = self.base_archetype.update(self, **kwargs)
        # invoke any automated workflow transitions after update
        triggerAutomaticTransitions(self)

    edit = update

    def at_post_edit_script(self):
        # XXX Need to remove this once we have real events
        # invoke any automated workflow transitions after update
        triggerAutomaticTransitions(self)
    
    # we are doing the same thing on create
    at_post_create_script = at_post_edit_script

    def condMakePrivate(self):
        """
        show the checkbox iff the member is in a public or private state
        and has valid transitions
        """
        wft = getToolByName(self, 'portal_workflow')
        state = wft.getInfoFor(self, 'review_state')
        if state == 'public':
            transition = 'make_private'
        elif state == 'private':
            transition = 'make_public'
        else:
            return False
        for d in wft.getTransitionsFor(self):
            try:
                if d['id'] == transition: return True
            except KeyError:
                pass
        return False
            
    def getMakePrivate(self):
        """
        returns True if the member workflow state is private
        """
        wft = getToolByName(self, 'portal_workflow')
        return wft.getInfoFor(self, 'review_state') == 'private'

    def setMakePrivate(self, val):
        """
        set the make private visiblity flag
        """
        isPrivate = self.getMakePrivate()
        shouldBePrivate = bool(int(val))
        if isPrivate != shouldBePrivate:
            state = shouldBePrivate and 'make_private' or 'make_public'
            wft = getToolByName(self, 'portal_workflow')
            wft.doActionFor(self, state)

    def register(self):
        """
        perform any registration information necessary after a member is registered
        """
        rtool = getToolByName(self, 'portal_registration')
        site_props = getToolByName(self, 'portal_properties').site_properties
        
        # XXX unicode names break sending the email
        unicode_name = self.getFullname()
        self.setFullname(str(unicode_name))
        if site_props.validate_email or self.getMail_me():
            rtool.registeredNotify(self.getUserName())

        self.setFullname(unicode_name)

    def isVisible_ids(self):
        """
        condition to check if short names should be specified on the edit screen
        """
        props = getToolByName(self, 'portal_properties').site_properties
        return props.visible_ids

    # XXX: login is extraneous but needed for existing adapter
    def delete(self, login):
        self.aq_inner.aq_parent.manage_delObjects([self.getId()])

    def getListedProperty(self):
        """
        Used for member searching. Check permissions on viewing the object
        """
        return _checkPermission(cmfpermissions.View, self)

InitializeClass(BaseMember)


class Member(BrowserDefaultMixin, BaseMember, atapi.BaseContent):
    """
    A regular non-folderish member content object.
    """
    security = ClassSecurityInfo()
    base_archetype = atapi.BaseContent

    # BBB: needed to get AT's installTypes to work
    __implements__ = BrowserDefaultMixin.__implements__ + \
                     atapi.BaseContent.__implements__

    def __call__(self, *args, **kwargs):
        """
        Prevents infinite recursion when using member object as a
        boolean value in TALES expressions.
        """
        return self.getId()

atapi.registerType(Member)
InitializeClass(Member)


class FolderishMember(BrowserDefaultMixin, BaseMember, atapi.BaseFolder):
    """
    A regular folderish member content object.
    """
    security = ClassSecurityInfo()
    base_archetype = atapi.BaseFolder

    # BBB: needed to get AT's installTypes to work
    __implements__ = BrowserDefaultMixin.__implements__ + \
                     atapi.BaseFolder.__implements__

    def __call__(self, *args, **kwargs):
        """
        Prevents infinite recursion when using member object as a
        boolean value in TALES expressions.
        """
        return self.getId()

atapi.registerType(Member)
InitializeClass(Member)
