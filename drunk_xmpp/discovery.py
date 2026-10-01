"""
Service Discovery module for DrunkXMPP.

XEP-0030: Service Discovery (server and room features)
XEP-0092: Software Version (server version queries)

Provides methods for querying server capabilities, MUC room features,
and server software information.
"""

from typing import Dict, Any, Optional
from slixmpp.exceptions import IqError, IqTimeout


class DiscoveryMixin:
    """
    Mixin providing service discovery functionality.

    Requirements (provided by DrunkXMPP):
    - self.plugin: Dict of loaded slixmpp plugins
    - self.boundjid: Current bound JID
    - self.logger: Logger instance
    """

    # ============================================================================
    # XEP-0030: Service Discovery (MUC Room Features)
    # ============================================================================

    async def get_room_info(self, room_jid: str) -> Optional[str]:
        """
        Get room name from disco#info identity.

        Args:
            room_jid: Room JID to query

        Returns:
            Room name from identity, or None if not available
        """
        try:
            xep_0030 = self.plugin['xep_0030']
            info = await xep_0030.get_info(jid=room_jid, timeout=10)

            # Extract room name from identity
            identities = info['disco_info']['identities']
            for identity in identities:
                # Identity tuple is (category, type, xml_lang, name)
                if identity[0] == 'conference' and identity[1] == 'text':
                    room_name = identity[3]  # name field
                    if room_name:
                        self.logger.debug(f"Room {room_jid} name: {room_name}")
                        return room_name

            return None

        except Exception as e:
            self.logger.debug(f"Failed to get room name for {room_jid}: {e}")
            return None

    async def get_room_features(self, room_jid: str) -> Dict[str, Any]:
        """
        Query MUC room features via disco#info to determine OMEMO compatibility.

        Per XEP-0384, OMEMO in MUCs requires:
        - MUST be muc_nonanonymous (non-anonymous room)
        - SHOULD be muc_membersonly (members-only room)

        Args:
            room_jid: Room JID to query

        Returns:
            Dict with keys:
            - name: str - Room name from disco#info identity (if available)
            - features: List[str] - All features supported by the room
            - muc_nonanonymous: bool - Room is non-anonymous (required for OMEMO)
            - muc_membersonly: bool - Room is members-only (recommended for OMEMO)
            - muc_open: bool - Room is open (anyone can join)
            - muc_passwordprotected: bool - Room requires password
            - muc_hidden: bool - Room is hidden from search
            - muc_public: bool - Room is public
            - muc_persistent: bool - Room persists when empty
            - muc_moderated: bool - Room is moderated
            - muc_unsecured: bool - Room does not require password
            - muc_unmoderated: bool - Room is unmoderated
            - supports_omemo: bool - Room meets OMEMO requirements (nonanonymous + membersonly)
        """
        try:
            xep_0030 = self.plugin['xep_0030']

            # Query disco#info for the room
            info = await xep_0030.get_info(jid=room_jid, timeout=10)

            # Extract room name from identity
            room_name = None
            identities = info['disco_info']['identities']
            for identity in identities:
                # Identity tuple is (category, type, xml_lang, name)
                if identity[0] == 'conference' and identity[1] == 'text':
                    room_name = identity[3]  # name field
                    break

            # Extract features
            features = list(info['disco_info']['features'])

            # Check specific MUC features
            result = {
                'name': room_name,
                'features': features,
                'muc_nonanonymous': 'muc_nonanonymous' in features,
                'muc_membersonly': 'muc_membersonly' in features,
                'muc_open': 'muc_open' in features,
                'muc_passwordprotected': 'muc_passwordprotected' in features,
                'muc_hidden': 'muc_hidden' in features,
                'muc_public': 'muc_public' in features,
                'muc_persistent': 'muc_persistent' in features,
                'muc_moderated': 'muc_moderated' in features,
                'muc_unsecured': 'muc_unsecured' in features,
                'muc_unmoderated': 'muc_unmoderated' in features,
            }

            # Parse extended disco#info (XEP-0128) for additional room info
            # This contains muc#roomconfig_changesubject and other settings
            try:
                # Get the data form from disco#info via XEP-0128 (Extended Service Discovery)
                # XEP-0128 registers Form as a stanza plugin to DiscoInfo with iterable=True
                disco_stanza = info['disco_info']

                # Forms are iterable, so we need to find the right one
                form_found = False
                for form in disco_stanza['substanzas']:
                    # Check if this is a Form stanza (plugin_attrib='form')
                    if hasattr(form, 'plugin_attrib') and form.plugin_attrib == 'form':
                        values = form.get_values()

                        # Check if this is a MUC roominfo form
                        # FORM_TYPE may be a list or a string depending on slixmpp version
                        form_type = values.get('FORM_TYPE')
                        if isinstance(form_type, list):
                            form_type = form_type[0] if form_type else None

                        if form_type == 'http://jabber.org/protocol/muc#roominfo':
                            form_found = True
                            # Parse changesubject setting (allows participants to change subject)
                            # Value may already be a boolean or could be a string '0'/'1'
                            changesubject_val = values.get('muc#roomconfig_changesubject', False)

                            # Handle boolean, string, or other types
                            if isinstance(changesubject_val, bool):
                                result['allow_subject_change'] = changesubject_val
                            else:
                                result['allow_subject_change'] = str(changesubject_val).lower() in ('1', 'true')
                            break

                if not form_found:
                    result['allow_subject_change'] = False
            except Exception as e:
                self.logger.debug(f"No extended room info form in disco#info for {room_jid}: {e}")
                result['allow_subject_change'] = False

            # XEP-0384: OMEMO requires non-anonymous, recommends members-only
            result['supports_omemo'] = (
                result['muc_nonanonymous'] and
                result['muc_membersonly']
            )

            self.logger.info(
                f"Room {room_jid}: nonanonymous={result['muc_nonanonymous']}, "
                f"membersonly={result['muc_membersonly']}, "
                f"supports_omemo={result['supports_omemo']}"
            )

            return result

        except IqError as e:
            error_condition = e.iq['error']['condition']
            self.logger.warning(f"Failed to query room features for {room_jid}: {error_condition}")
            return {
                'features': [],
                'muc_nonanonymous': False,
                'muc_membersonly': False,
                'muc_open': False,
                'muc_passwordprotected': False,
                'muc_hidden': False,
                'muc_public': False,
                'muc_persistent': False,
                'muc_moderated': False,
                'muc_unsecured': False,
                'muc_unmoderated': False,
                'supports_omemo': False,
                'error': error_condition
            }
        except IqTimeout:
            self.logger.warning(f"Timeout querying room features for {room_jid}")
            return {
                'features': [],
                'muc_nonanonymous': False,
                'muc_membersonly': False,
                'muc_open': False,
                'muc_passwordprotected': False,
                'muc_hidden': False,
                'muc_public': False,
                'muc_persistent': False,
                'muc_moderated': False,
                'muc_unsecured': False,
                'muc_unmoderated': False,
                'supports_omemo': False,
                'error': 'timeout'
            }
        except Exception as e:
            self.logger.exception(f"Failed to query room features for {room_jid}: {e}")
            return {
                'features': [],
                'muc_nonanonymous': False,
                'muc_membersonly': False,
                'muc_open': False,
                'muc_passwordprotected': False,
                'muc_hidden': False,
                'muc_public': False,
                'muc_persistent': False,
                'muc_moderated': False,
                'muc_unsecured': False,
                'muc_unmoderated': False,
                'supports_omemo': False,
                'error': str(e)
            }

    async def get_room_config(self, room_jid: str) -> Optional[Dict[str, Any]]:
        """
        Query MUC room configuration form (XEP-0045 §10).

        Fetches the owner configuration form for detailed room settings.
        This is separate from disco#info features and requires owner permissions.

        Args:
            room_jid: Room JID to query

        Returns:
            Dict with room configuration, or None if unavailable:
            {
                'roomname': str,            # from muc#roomconfig_roomname
                'roomdesc': str,            # from muc#roomconfig_roomdesc
                'persistent': bool,         # from muc#roomconfig_persistentroom
                'public': bool,             # from muc#roomconfig_publicroom
                'moderated': bool,          # from muc#roomconfig_moderatedroom
                'membersonly': bool,        # from muc#roomconfig_membersonly
                'password_protected': bool, # from muc#roomconfig_passwordprotectedroom
                'max_users': int or None,   # from muc#roomconfig_maxusers (None = unlimited)
                'allow_invites': bool,      # from muc#roomconfig_allowinvites
                'allow_subject_change': bool, # from muc#roomconfig_changesubject
                'enable_logging': bool,     # from muc#roomconfig_enablelogging
                'whois': str,               # 'anyone' or 'moderators' (from muc#roomconfig_whois)
                'error': str or None        # Error message if query failed
            }

        Notes:
            - Requires owner permissions (will fail for non-owners)
            - Not all servers expose room config (some return forbidden)
            - Boolean values are normalized to Python bool
            - Returns None if room doesn't support config queries
        """
        try:
            xep_0045 = self.plugin['xep_0045']

            # Query room config form (XEP-0045 §10.2)
            self.logger.debug(f"Querying room configuration for {room_jid}")
            form = await xep_0045.get_room_config(room=room_jid, timeout=10)

            # Extract form values
            values = form.get_values()

            # Helper to parse boolean values (forms return '0', '1', 'true', 'false')
            def parse_bool(value, default=False):
                if value is None:
                    return default
                if isinstance(value, bool):
                    return value
                return str(value).lower() in ('1', 'true')

            # Helper to parse integer (with None for unlimited)
            def parse_int(value, default=None):
                if value is None or value == '' or str(value).lower() == 'none':
                    return default
                try:
                    return int(value)
                except (ValueError, TypeError):
                    return default

            # Map form fields to result dict
            result = {
                'roomname': values.get('muc#roomconfig_roomname', ''),
                'roomdesc': values.get('muc#roomconfig_roomdesc', ''),
                'persistent': parse_bool(values.get('muc#roomconfig_persistentroom'), False),
                'public': parse_bool(values.get('muc#roomconfig_publicroom'), False),
                'moderated': parse_bool(values.get('muc#roomconfig_moderatedroom'), False),
                'membersonly': parse_bool(values.get('muc#roomconfig_membersonly'), False),
                'password_protected': parse_bool(values.get('muc#roomconfig_passwordprotectedroom'), False),
                'max_users': parse_int(values.get('muc#roomconfig_maxusers')),
                'allow_invites': parse_bool(values.get('muc#roomconfig_allowinvites'), True),
                'allow_subject_change': parse_bool(values.get('muc#roomconfig_changesubject'), False),
                'enable_logging': parse_bool(values.get('muc#roomconfig_enablelogging'), False),
                'whois': values.get('muc#roomconfig_whois', 'moderators'),  # 'anyone' or 'moderators'
                'error': None
            }

            self.logger.info(
                f"Room {room_jid} config: persistent={result['persistent']}, "
                f"public={result['public']}, moderated={result['moderated']}, "
                f"max_users={result['max_users']}"
            )

            return result

        except ValueError as e:
            # Room doesn't provide config form
            self.logger.debug(f"Room {room_jid} does not provide config form: {e}")
            return {
                'error': 'Config form not available',
                'roomname': None,
                'roomdesc': None,
                'persistent': False,
                'public': False,
                'moderated': False,
                'membersonly': False,
                'password_protected': False,
                'max_users': None,
                'allow_invites': True,
                'allow_subject_change': False,
                'enable_logging': False,
                'whois': 'moderators'
            }
        except IqError as e:
            error_condition = e.iq['error']['condition']
            self.logger.warning(f"Failed to query room config for {room_jid}: {error_condition}")

            # Distinguish permission errors from other errors
            if error_condition == 'forbidden':
                error_msg = 'Permission denied (owner-only)'
            else:
                error_msg = f'IQ error: {error_condition}'

            return {
                'error': error_msg,
                'roomname': None,
                'roomdesc': None,
                'persistent': False,
                'public': False,
                'moderated': False,
                'membersonly': False,
                'password_protected': False,
                'max_users': None,
                'allow_invites': True,
                'allow_subject_change': False,
                'enable_logging': False,
                'whois': 'moderators'
            }
        except IqTimeout:
            self.logger.warning(f"Timeout querying room config for {room_jid}")
            return {
                'error': 'Timeout',
                'roomname': None,
                'roomdesc': None,
                'persistent': False,
                'public': False,
                'moderated': False,
                'membersonly': False,
                'password_protected': False,
                'max_users': None,
                'allow_invites': True,
                'allow_subject_change': False,
                'enable_logging': False,
                'whois': 'moderators'
            }
        except Exception as e:
            self.logger.exception(f"Unexpected error querying room config for {room_jid}: {e}")
            return {
                'error': str(e),
                'roomname': None,
                'roomdesc': None,
                'persistent': False,
                'public': False,
                'moderated': False,
                'membersonly': False,
                'password_protected': False,
                'max_users': None,
                'allow_invites': True,
                'allow_subject_change': False,
                'enable_logging': False,
                'whois': 'moderators'
            }

    async def set_room_config(self, room_jid: str, config: Dict[str, Any]) -> bool:
        """
        Submit room configuration to server (XEP-0045 §10).

        Args:
            room_jid: Room JID to configure
            config: Dict with configuration fields:
                {
                    'roomname': str,
                    'roomdesc': str,
                    'membersonly': bool,
                    'moderatedroom': bool,
                    'passwordprotectedroom': bool,
                    'roomsecret': str (password),
                    'maxusers': int or None,
                    'persistentroom': bool,
                    'publicroom': bool,
                    'enablelogging': bool,
                }

        Returns:
            True if config was successfully submitted, False otherwise
        """
        try:
            xep_0045 = self.plugin['xep_0045']

            # Get current config form to submit modified values
            self.logger.info(f"Setting room config for {room_jid}: {config}")
            form = await xep_0045.get_room_config(room=room_jid, timeout=10)

            # Log available fields
            self.logger.debug(f"Available form fields: {list(form['values'].keys())}")

            # Update form values with new config
            fields = form.get_fields()
            for key, value in config.items():
                # Map our simplified keys to muc#roomconfig_* field names
                field_name = f'muc#roomconfig_{key}'

                # Check if field exists in server's form
                if field_name not in fields:
                    self.logger.warning(f"Skipping {field_name} - not supported by server")
                    continue

                # Special handling for different field types
                if isinstance(value, bool):
                    # Convert bool to string for form submission
                    fields[field_name]['value'] = '1' if value else '0'
                elif value is None:
                    # None means use server default (usually unlimited for maxusers)
                    fields[field_name]['value'] = ''
                else:
                    # String or int values
                    fields[field_name]['value'] = str(value)

                self.logger.info(f"Setting {field_name} = {fields[field_name]['value']} (original: {value})")

            # Submit the configured form
            self.logger.debug(f"Submitting room configuration for {room_jid}")
            await xep_0045.set_room_config(room=room_jid, config=form, timeout=15)

            self.logger.info(f"Successfully updated room configuration for {room_jid}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to set room config for {room_jid}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False

    async def get_muc_service(self) -> Optional[str]:
        """
        Find the group chat service of our server (XEP-0030).

        Asks disco#items on the account domain, then disco#info on each
        item. The first item with identity conference/text wins.
        The result is kept until the session ends.

        Returns:
            Service JID (e.g. 'conference.example.com'), or None if not found
        """
        cached = getattr(self, '_muc_service', None)
        if cached:
            return cached

        xep_0030 = self.plugin['xep_0030']
        domain = self.boundjid.domain
        try:
            items = await xep_0030.get_items(jid=domain, timeout=10)
        except (IqError, IqTimeout) as e:
            self.logger.warning(f"disco#items on {domain} failed: {e}")
            return None

        for item in items['disco_items']['items']:
            # Item tuple is (jid, node, name)
            item_jid = str(item[0])
            try:
                info = await xep_0030.get_info(jid=item_jid, timeout=5)
            except Exception as e:
                # One bad item must not stop the search
                self.logger.debug(f"disco#info on {item_jid} failed: {e}")
                continue
            for identity in info['disco_info']['identities']:
                # Identity tuple is (category, type, xml_lang, name)
                if identity[0] == 'conference' and identity[1] == 'text':
                    self.logger.info(f"Group chat service of {domain}: {item_jid}")
                    self._muc_service = item_jid
                    return item_jid

        self.logger.info(f"No group chat service found on {domain}")
        return None

    # ============================================================================
    # XEP-0092: Software Version
    # ============================================================================

    async def get_server_version(self) -> Dict[str, Optional[str]]:
        """
        Query server software version via XEP-0092.

        Returns:
            Dict with keys:
            - name: Server software name (str, optional)
            - version: Server version number (str, optional)
            - os: Operating system info (str, optional)
            - error: Error message if query failed (str, optional)
        """
        try:
            xep_0092 = self.plugin['xep_0092']

            # Query server version (use bare server JID)
            server_jid = self.boundjid.domain
            version_info = await xep_0092.get_version(jid=server_jid, timeout=10)

            result = {
                'name': version_info.get('software_version', {}).get('name'),
                'version': version_info.get('software_version', {}).get('version'),
                'os': version_info.get('software_version', {}).get('os'),
                'error': None
            }

            self.logger.info(
                f"Server version: {result['name']} {result['version']}"
                + (f" ({result['os']})" if result['os'] else "")
            )

            return result

        except IqTimeout:
            self.logger.warning(f"Timeout querying server version from {self.boundjid.domain}")
            return {
                'name': None,
                'version': None,
                'os': None,
                'error': 'Timeout'
            }
        except IqError as e:
            error_condition = e.iq['error']['condition']
            self.logger.warning(f"Failed to query server version: {error_condition}")
            return {
                'name': None,
                'version': None,
                'os': None,
                'error': error_condition
            }
        except Exception as e:
            self.logger.exception(f"Unexpected error querying server version: {e}")
            return {
                'name': None,
                'version': None,
                'os': None,
                'error': str(e)
            }

    # ============================================================================
    # XEP-0030: Service Discovery (Server Features)
    # ============================================================================

    async def get_server_features(self) -> Dict[str, Any]:
        """
        Query server features via XEP-0030 disco#info.

        Returns XEP support by mapping feature namespaces to XEP numbers.

        Returns:
            Dict with keys:
            - features: List[str] - All feature namespaces supported by server
            - xeps: List[Dict] - List of recognized XEPs with 'number' and 'name' keys
            - identities: List[Dict] - Server identities with 'category', 'type', 'name'
            - error: Error message if query failed (str, optional)
        """
        # XEP namespace to (number, name) mapping
        XEP_MAP = {
            'jabber:iq:version': ('0092', 'Software Version'),
            'http://jabber.org/protocol/disco#info': ('0030', 'Service Discovery'),
            'http://jabber.org/protocol/disco#items': ('0030', 'Service Discovery'),
            'http://jabber.org/protocol/muc': ('0045', 'Multi-User Chat'),
            'jabber:iq:register': ('0077', 'In-Band Registration'),
            'jabber:iq:search': ('0055', 'Jabber Search'),
            'http://jabber.org/protocol/commands': ('0050', 'Ad-Hoc Commands'),
            'http://jabber.org/protocol/rsm': ('0059', 'Result Set Management'),
            'http://jabber.org/protocol/pubsub': ('0060', 'Publish-Subscribe'),
            'http://jabber.org/protocol/pubsub#publish': ('0060', 'Publish-Subscribe'),
            'vcard-temp': ('0054', 'vcard-temp'),
            'jabber:iq:last': ('0012', 'Last Activity'),
            'jabber:iq:private': ('0049', 'Private XML Storage'),
            'urn:xmpp:blocking': ('0191', 'Blocking Command'),
            'urn:xmpp:carbons:2': ('0280', 'Message Carbons'),
            'urn:xmpp:mam:2': ('0313', 'Message Archive Management'),
            'urn:xmpp:ping': ('0199', 'XMPP Ping'),
            'urn:xmpp:receipts': ('0184', 'Message Delivery Receipts'),
            'urn:xmpp:sid:0': ('0359', 'Unique and Stable Stanza IDs'),
            'http://jabber.org/protocol/chatstates': ('0085', 'Chat State Notifications'),
            'urn:xmpp:chat-markers:0': ('0333', 'Chat Markers'),
            'urn:xmpp:message-correct:0': ('0308', 'Last Message Correction'),
            'urn:xmpp:http:upload:0': ('0363', 'HTTP File Upload'),
            'eu:siacs:conversations:http:upload': ('0363', 'HTTP File Upload (Conversations)'),
            'urn:xmpp:avatar:metadata': ('0084', 'User Avatar'),
            'urn:xmpp:avatar:data': ('0084', 'User Avatar'),
            'http://jabber.org/protocol/mood': ('0107', 'User Mood'),
            'http://jabber.org/protocol/activity': ('0108', 'User Activity'),
            'http://jabber.org/protocol/tune': ('0118', 'User Tune'),
            'urn:xmpp:time': ('0202', 'Entity Time'),
            'urn:xmpp:delay': ('0203', 'Delayed Delivery'),
            'jabber:x:data': ('0004', 'Data Forms'),
            'jabber:x:oob': ('0066', 'Out of Band Data'),
            'urn:xmpp:forward:0': ('0297', 'Stanza Forwarding'),
            'urn:xmpp:attention:0': ('0224', 'Attention'),
            'eu:siacs:conversations:omemo:1': ('0384', 'OMEMO (Siacs 0.8.0+)'),
            'urn:xmpp:omemo:2': ('0384', 'OMEMO 0.8.0+'),
            'urn:xmpp:omemo:1': ('0384', 'OMEMO 0.3.0'),
            'http://jabber.org/protocol/caps': ('0115', 'Entity Capabilities'),
            'urn:xmpp:bookmarks:1': ('0402', 'PEP Native Bookmarks'),
            'urn:xmpp:bookmarks:0': ('0402', 'PEP Native Bookmarks (legacy)'),
            'http://jabber.org/protocol/compress': ('0138', 'Stream Compression'),
            'urn:ietf:params:xml:ns:xmpp-bind': ('RFC 6120', 'Resource Binding'),
            'urn:ietf:params:xml:ns:xmpp-session': ('RFC 3921', 'Session Establishment'),
            'urn:ietf:params:xml:ns:xmpp-tls': ('RFC 6120', 'STARTTLS'),
            'urn:ietf:params:xml:ns:xmpp-sasl': ('RFC 6120', 'SASL Authentication'),
            'urn:xmpp:sm:3': ('0198', 'Stream Management'),
            'urn:xmpp:sm:2': ('0198', 'Stream Management'),
            'jabber:iq:roster': ('RFC 6121', 'Roster Management'),
            'msgoffline': ('0160', 'Offline Message Storage'),
            'jabber:iq:privacy': ('0016', 'Privacy Lists'),
            'urn:xmpp:csi:0': ('0352', 'Client State Indication'),
            'urn:xmpp:push:0': ('0357', 'Push Notifications'),
            'urn:xmpp:jingle:1': ('0166', 'Jingle'),
            'urn:xmpp:jingle:apps:rtp:1': ('0167', 'Jingle RTP Sessions'),
            'urn:xmpp:jingle:apps:rtp:audio': ('0167', 'Jingle Audio'),
            'urn:xmpp:jingle:apps:rtp:video': ('0167', 'Jingle Video'),
            'urn:xmpp:jingle:transports:ice-udp:1': ('0176', 'Jingle ICE-UDP Transport'),
            'http://jabber.org/protocol/ibb': ('0047', 'In-Band Bytestreams'),
            'http://jabber.org/protocol/bytestreams': ('0065', 'SOCKS5 Bytestreams'),
            'urn:xmpp:hashes:2': ('0300', 'Cryptographic Hash Functions'),
            'urn:xmpp:hash-function-text-names:sha-256': ('0300', 'SHA-256'),
            'urn:xmpp:reactions:0': ('0444', 'Message Reactions'),
            'urn:xmpp:reply:0': ('0461', 'Message Replies'),
            'urn:xmpp:fallback:0': ('0428', 'Fallback Indication'),
            'urn:xmpp:eme:0': ('0380', 'Explicit Message Encryption'),
            'urn:xmpp:sfs:0': ('0447', 'Stateless File Sharing'),
            'urn:xmpp:sims:1': ('0385', 'Stateless Inline Media Sharing'),
        }

        try:
            xep_0030 = self.plugin['xep_0030']

            # Query disco#info for the server
            server_jid = self.boundjid.domain
            info = await xep_0030.get_info(jid=server_jid, timeout=10)

            # Extract features
            features = list(info['disco_info']['features'])

            # Extract identities
            identities = []
            for identity in info['disco_info']['identities']:
                identities.append({
                    'category': identity[0],  # e.g., 'server'
                    'type': identity[1],      # e.g., 'im'
                    'name': identity[2] if len(identity) > 2 else None
                })

            # Map features to XEPs
            xeps = []
            seen_xeps = set()
            for feature in features:
                if feature in XEP_MAP:
                    xep_num, xep_name = XEP_MAP[feature]
                    # Avoid duplicates (some XEPs have multiple namespaces)
                    if xep_num not in seen_xeps:
                        xeps.append({
                            'number': xep_num,
                            'name': xep_name,
                            'namespace': feature
                        })
                        seen_xeps.add(xep_num)

            # Sort by XEP number
            xeps.sort(key=lambda x: x['number'])

            result = {
                'features': features,
                'xeps': xeps,
                'identities': identities,
                'error': None
            }

            self.logger.info(
                f"Server {server_jid}: {len(features)} features, {len(xeps)} recognized XEPs"
            )

            return result

        except IqTimeout:
            self.logger.warning(f"Timeout querying server features from {self.boundjid.domain}")
            return {
                'features': [],
                'xeps': [],
                'identities': [],
                'error': 'Timeout'
            }
        except IqError as e:
            error_condition = e.iq['error']['condition']
            self.logger.warning(f"Failed to query server features: {error_condition}")
            return {
                'features': [],
                'xeps': [],
                'identities': [],
                'error': error_condition
            }
        except Exception as e:
            self.logger.exception(f"Unexpected error querying server features: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return {
                'features': [],
                'xeps': [],
                'identities': [],
                'error': str(e)
            }
