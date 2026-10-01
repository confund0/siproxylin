"""
Bookmarks module for DrunkXMPP.

XEP-0402: PEP Native Bookmarks

Provides methods for managing server-side MUC room bookmarks.
"""

from typing import List, Dict, Any, Optional
from slixmpp.exceptions import IqError
from slixmpp.plugins.xep_0004 import Form


BOOKMARKS_NODE = 'urn:xmpp:bookmarks:1'

# Node config from XEP-0402: keep all items, only the owner can read them
BOOKMARKS_NODE_CONFIG = {
    'pubsub#persist_items': 'true',
    'pubsub#max_items': 'max',
    'pubsub#send_last_published_item': 'never',
    'pubsub#access_model': 'whitelist',
}


def _config_form(form_type: str) -> Form:
    """Build a publish-options or node_config form with the bookmarks node config."""
    form = Form()
    form['type'] = 'submit'
    form.add_field(var='FORM_TYPE', ftype='hidden', value=form_type)
    for var, value in BOOKMARKS_NODE_CONFIG.items():
        form.add_field(var=var, value=value)
    return form


class BookmarksMixin:
    """
    Mixin providing bookmarks management functionality.

    Requirements (provided by DrunkXMPP):
    - self.plugin: Dict of loaded slixmpp plugins
    - self.boundjid: Current bound JID
    - self.logger: Logger instance
    """

    # ============================================================================
    # XEP-0402: PEP Native Bookmarks
    # ============================================================================

    async def get_bookmarks(self) -> List[Dict[str, Any]]:
        """
        Retrieve bookmarks from server using PEP Native Bookmarks (XEP-0402).

        Returns:
            List of bookmark dicts with keys:
            - jid: Room JID
            - name: Bookmark name
            - nick: Nickname to use
            - password: Room password (if any)
            - autojoin: Boolean indicating if room should be auto-joined
        """
        try:
            xep_0060 = self.plugin['xep_0060']

            # Retrieve bookmarks from PEP node
            # For PEP, jid is our own bare JID, node is the bookmarks namespace
            result = await xep_0060.get_items(
                jid=self.boundjid.bare,
                node='urn:xmpp:bookmarks:1',
                timeout=10
            )

            bookmarks = []
            for item in result['pubsub']['items']:
                conf = item['conference']
                if conf:
                    bookmarks.append({
                        'jid': item['id'],  # Item ID is the room JID
                        'name': conf.get('name', ''),
                        'nick': conf.get('nick', ''),
                        'password': conf.get('password', ''),
                        'autojoin': conf.get('autojoin', False)
                    })

            self.logger.info(f"Retrieved {len(bookmarks)} bookmarks")
            return bookmarks

        except IqError as e:
            # Node might not exist yet (no bookmarks)
            if e.iq['error']['condition'] == 'item-not-found':
                self.logger.info("No bookmarks found (node doesn't exist)")
                return []
            self.logger.warning(f"Failed to retrieve bookmarks: {e.iq['error']['condition']}")
            return []
        except Exception as e:
            self.logger.exception(f"Failed to retrieve bookmarks: {e}")
            return []

    async def add_bookmark(self, jid: str, name: str, nick: str,
                          password: Optional[str] = None, autojoin: bool = True):
        """
        Add or update a bookmark on the server.

        Args:
            jid: Room JID to bookmark
            name: Display name for the bookmark
            nick: Nickname to use in the room
            password: Optional room password
            autojoin: Whether to auto-join this room on login (default: True)
        """
        try:
            xep_0060 = self.plugin['xep_0060']

            # Create conference element
            from slixmpp.plugins.xep_0402.stanza import Conference

            conf = Conference()
            conf['name'] = name
            conf['autojoin'] = autojoin
            if nick:
                conf['nick'] = nick
            if password:
                conf['password'] = password

            # Publish to bookmarks node. The publish-options create a new
            # node with the right config, or check the config of the node.
            options = _config_form('http://jabber.org/protocol/pubsub#publish-options')
            try:
                await xep_0060.publish(
                    jid=self.boundjid.bare,
                    node=BOOKMARKS_NODE,
                    id=jid,
                    payload=conf,
                    options=options,
                    timeout=10
                )
            except IqError as e:
                precondition = e.iq['error'].xml.find(
                    '{http://jabber.org/protocol/pubsub#errors}precondition-not-met')
                if precondition is None:
                    raise
                # The node exists with another config (for example the server
                # default: contacts can read it). Set the config, publish again.
                self.logger.info("Bookmarks node has another config: setting the node config")
                await xep_0060.set_node_config(
                    jid=self.boundjid.bare,
                    node=BOOKMARKS_NODE,
                    config=_config_form('http://jabber.org/protocol/pubsub#node_config'),
                    timeout=10
                )
                await xep_0060.publish(
                    jid=self.boundjid.bare,
                    node=BOOKMARKS_NODE,
                    id=jid,
                    payload=conf,
                    options=_config_form('http://jabber.org/protocol/pubsub#publish-options'),
                    timeout=10
                )

            self.logger.info(f"Added/updated bookmark: {jid} (autojoin={autojoin})")

        except IqError as e:
            error_condition = e.iq['error']['condition']
            self.logger.warning(f"Failed to add bookmark: {error_condition}")
            raise
        except Exception as e:
            self.logger.exception(f"Failed to add bookmark: {e}")
            raise

    async def remove_bookmark(self, jid: str):
        """
        Remove a bookmark from the server.

        Args:
            jid: Room JID to remove from bookmarks
        """
        try:
            xep_0060 = self.plugin['xep_0060']

            # Delete item from bookmarks node; notify tells our other clients
            await xep_0060.retract(
                jid=self.boundjid.bare,
                node=BOOKMARKS_NODE,
                id=jid,
                notify=True,
                timeout=10
            )

            self.logger.info(f"Removed bookmark: {jid}")

        except IqError as e:
            error_condition = e.iq['error']['condition']
            self.logger.warning(f"Failed to remove bookmark: {error_condition}")
            raise
        except Exception as e:
            self.logger.exception(f"Failed to remove bookmark: {e}")
            raise
