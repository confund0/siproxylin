# Changelog

All notable changes to Siproxylin are documented in this file.

---

## [0.0.20 - High-Proof-Moonshine] - 2026-02-28

> (795b2bfabc)

    Improvements to emoji picker

> (1986bcb9a3)

    Adding Tools->Disco for admins to query on arbitrary JIDs

> (3a8052fd65)

    Merge pull request #1 from weiss/fix/app-image-url
    
    README.md: Fix link to latest AppImage

> (e59baa9209)

    README.md: Fix link to latest AppImage

## [0.0.19 - High-Proof-Moonshine] - 2026-02-24

> (6855a245ea)

    Added feature to quickly query "disco"

> (d0ea28bb83)

    Added PyYAML to requirements.txt

> (65feb070dc)

    Add Service Discovery (Disco) admin tool
    
    - New checkbox Settings → Advanced → Enable Admin Tools
    - Context menu 'Disco' on contacts/MUCs/accounts (when Admin Tools enabled)
    - Shows original server XML response, pretty-formatted
    - Has YAML/XML toggle display modes

## [0.0.18 - High-Proof-Moonshine] - 2026-02-24

> (f419566c12)

    Releasing v0.0.18 (critical bugfix)

> (2ae7bad05b)

    Important fix of misuse of slixmpp API causing spurious XML elements and potentially leading to message loss

> (6d807d8dda)

    Registered git hook which creates CHANGELOG.md automatically

## [0.0.17 - Double-Distilled] - 2026-02-24

> (0c01ad0264)

    Releasing v0.0.17

> (406c748a60)

    Wired up "mute" button (call window)

> (abbaca5e8e)

    Added "Copy JID" context menu to contacts

> (b0241c6ea5)

    Fix "OMEMO key trust" UI interaction with backend

> (bb6c224ecf)

    Adding a document that explains OMEMO use with Siproxylin

> (7e58476153)

    Fixed a regression bug introduced by dialog refactoring which prevented OMEMO keys from being displayed in the contact details

> (139608c19c)

    Add image paste feature with EXIF stripping
    
    - Intercept image pastes from clipboard (Ctrl+V)
    - Preserve original format (PNG→PNG, JPEG→JPEG, WebP, BMP)
    - Strip EXIF metadata automatically for privacy (Qt behavior)
    - Safe temp file cleanup via tracking list
    - Support quality=95 for JPEG to minimize compression artifacts
    - Red paperclip indicator with filename display (like attached with
      button)
    - Cleanup on success, failure, cancel, and conversation switch

## [0.0.16 - Double-Distilled] - 2026-02-23

> (df8b6a6224)

    MUC handling improvements

> (079709076e)

    Add MUC room destruction feature
    
    Implements XEP-0045 §10.9 room destruction for room owners.
    
    Changes:
    - Add destroy_room() wrapper in DrunkXMPP client (drunk_xmpp/client.py)
    - Add destroy_room() method in MucBarrel with full cleanup
      - Destroys room on server
      - Removes bookmark from server (XEP-0402)
      - Cleans local DB (bookmark, roster, conversation with CASCADE)
      - Emits roster_updated signal
    - Add "Destroy Room" button in MUC Details Dialog Settings tab
      - Dark red styling, visible only for owners of persistent rooms
      - Two-stage confirmation: warning + optional reason
    - Add destroy_muc() handler in MUCManager
      - Permission and connection checks
      - User-friendly error messages for common XMPP errors
      - Closes chat view if room was open
    - Wire signal: dialog → DialogManager → MainWindow → MUCManager
    
    Pattern follows barrel architecture: business logic in barrel,
    GUI handles only confirmation and error display.

> (48066c18da)

    Fix MUC join button for non-bookmarked rooms
    
    Fixes issue where "Join Room" button didn't appear or work for MUC rooms
    that exist in the conversation table but have no bookmark (e.g., after
    leaving a room via right-click "Leave" on old versions).

> (c8e0cafc9d)

    Add reverse MUC invite flow from Contact Manager
    
    Implements ability to invite contacts to MUC rooms from the Contact Manager
    dialog, complementing the existing room-first invite flow.
    
    User Flow:
    1. Open Contact Manager (Tools → Contacts)
    2. Select a contact
    3. Click "Invite to Room..." button
    4. SelectMucDialog opens:
       - Step 1: Select MUC room (shows ALL joined rooms across all accounts)
       - Step 2: Select which account to send invite from (filtered by room membership)
       - Step 3: Optional invitation message
    5. Invite sent via XMPP from chosen account
    
    Implementation:
    - NEW: SelectMucDialog with multi-account MUC selection
    - Added "Invite to Room..." button to Contact Manager (after "Open Chat")
    - Added MUCManager.invite_contact_to_room() method
    - Dynamic account selector based on selected room
    - Shows only accounts that are members of selected MUC
    
    Key Features:
    - Multi-account aware (can invite from any account joined to the room)
    - Permission-based sorting (highest permissions first)
    - Runtime state using client.joined_rooms (accurate)
    - Offline account detection

> (ba9a765479)

    Add MUC invite sending functionality
    
    Implements mediated MUC invitations (XEP-0045 §7.8.2) allowing users to
    invite contacts to group chats from the roster context menu.
    
    Implementation:
    - Added DrunkXMPP.send_muc_invite() wrapper for slixmpp's invite() method
    - Added MucBarrel.invite_to_room() API with connection checking
    - Created InviteContactDialog for JID input and optional reason field
    - Added "Invite Contact..." menu item to MUC context menu in roster
    - Integrated with MUCManager and MainWindow signal routing
    
    User Flow:
    1. Right-click MUC room in roster
    2. Click "Invite Contact..."
    3. Enter contact's JID and optional invitation message
    4. Invite sent via XMPP mediated invitation
    
    Technical Notes:
    - Passwords are NOT sent in invites (standard XMPP behavior per XEP-0045)
    - For password-protected rooms, communicate password separately
    - Dialog auto-enables Send button when JID entered
    - Error handling for offline accounts

> (3db20d4025)

    Fix MUC invite handling
    
    Fixes four critical bugs in MUC invitation flow:
    
    1. Missing signal registration (siproxylin/core/brewery.py)
       - Added muc_invite_received and muc_role_changed to _signals dict
       - Fixes KeyError crash when invite callback tried to emit signal
    
    2. Invite double-processing (drunk_xmpp/client.py)
       - Messages with <body> + MUC invite extension processed twice
       - Added check to skip MUC invites in _on_private_message handler
       - Prevents wrong conversation type (type=0 instead of type=1)
    
    3. Unwanted auto-join behavior (siproxylin/gui/managers/muc_manager.py)
       - Removed blocking dialog that auto-joined with autojoin=1
       - Changed to create bookmark with autojoin=0
       - Removed auto-join logic and unused helper method
       - User can now join manually via "Join Group" button
    
    4. Join button stuck in "Joining..." (siproxylin/gui/chat_view/taps/header.py)
       - Race condition between join success handler and refresh timer
       - Reset button text/state before hiding on successful join
       - Fixes stuck state in leave→re-invite→join scenario

> (d4c404d3c8)

    Fixed MUC Dialog Reason Label Duplication

## [0.0.15 - Double-Distilled] - 2026-02-23

> (ec0ed0fa54)

    Remove stale MUC cache from DB and clean dead code
    
    Remove obsolete muc_nonanonymous and muc_membersonly columns that were
    caching MUC OMEMO compatibility. These caused stale data bugs and are
    now replaced by live disco_cache reads.
    
    Code cleanup:
    - Remove unused encryption field read in _update_encryption_button_visibility()
    - Remove redundant DB write that duplicated header toggle handler
    - Update docstring to reflect actual behavior (uses disco_cache, not DB)
    
    Database migration v13→v14:
    - Drop conversation.muc_nonanonymous column
    - Drop conversation.muc_membersonly column
    - Update schema version to 14
    
    All MUC feature checks now use in-memory disco_cache exclusively,
    ensuring button state matches current room capabilities.

> (c7c736f07f)

    Make MUC config updates reflect in GUI properly
    
    Problem: When room configuration changed (e.g., toggling anonymity for
    OMEMO support), the encryption button didn't update for non-owner
    accounts and showed stale state based on database cache.
    
    Root causes:
    1. Wrong event handler - listening to groupchat_message instead of
       groupchat_config_status for MUC status codes
    2. Missing status codes - only handled 104, but servers send 173/174
       for privacy/anonymity changes (whois, logging, etc.)
    3. Stale database field - button visibility checked DB encryption field
       instead of live disco_cache, showing button even when room no
       longer supported OMEMO
    4. Signal timing - roster_updated emitted only after bookmark DB update,
       not immediately after disco_cache refresh
    
    Changes:
    - drunk_xmpp/client.py: Add groupchat_config_status handler for status
      codes 104, 172, 173, 174; update disco_cache in
      _handle_room_config_changed()
    - siproxylin/core/barrels/muc.py: Emit roster_updated immediately after
      disco_cache update; read muc_nonanonymous/membersonly from disco_cache
      in get_room_info()
    - siproxylin/gui/chat_view/chat_view.py: Read MUC features from
      disco_cache instead of DB encryption field for button visibility
    - siproxylin/gui/managers/roster_manager.py: Update button on
      roster_updated signal
    - siproxylin/gui/muc_details_dialog.py: Add 1.5s delay for server disco
      propagation after config save

## [0.0.14 - Double-Distilled] - 2026-02-12

> (69631abbd1)

    Fix MUC details dialog when disconnected
    
    Tab visibility based on joined status:
    - Not joined (gray MUC): Only show Settings tab (local bookmark settings)
    - Joined (blue MUC): Show Info + Participants + Settings + (Config if owner)
    - Settings tab always visible for editing bookmark details (name, nick, password)
    
    Disconnect handling fixes:
    - Clear joined_rooms on disconnect (fixes MUC staying blue after disconnect)
    - Skip live data fetch when disconnected (prevents timeouts)
    - Load cached data only when offline
    
    Async crash prevention:
    - Added _destroyed flag to prevent UI updates after dialog closed
    - Check flag before all async UI updates (disco fetch, config fetch, refresh)
    - Fixes crash: "Internal C++ object already deleted" when closing during operations

> (ef1e2c0603)

    Fix MUC visibility and joined/not-joined UX
    
    Chat list improvements:
    - Show MUCs with pending invitations (not just bookmarked/roster MUCs)
    - Show roster contacts even without messages (fixes new contact visibility)
    - Show conversations with messages even if not in roster (server messages)
    
    MUC color coding (theme-aware):
    - Blue: joined MUCs (presence='available')
    - Gray: not-joined MUCs (presence='unavailable')
    - Matches offline contact styling for consistency
    
    Leave Room behavior (privacy-focused):
    - Now deletes conversation + all messages (not just bookmark)
    - Updated confirmation dialog to reflect permanent deletion
    - Room disappears from chat list after leaving
    
    MUC details dialog:
    - Fix: Works for bookmarked MUCs even before joining
    - Query changed from conversation-based to bookmark-based with LEFT JOIN
    
    Fixes issues where:
    - New contacts didn't appear until messages exchanged
    - MUC invitations were invisible (unread counter with no chat)
    - Left MUCs remained in chat list due to lingering conversations
    - MUC details dialog failed for non-joined bookmarked rooms

> (673170dc6c)

    Adding few screenshots to README

## [0.0.13 - Double-Distilled] - 2026-02-11

> (d51cec6fc3)

    Bump version

> (a1924fef23)

    Fixes 1. Forgotten signal connections after main window refactioring; 2. Display messages from contacts that are not in roster;

> (91f881ec99)

    Update README.md

## [0.0.12 - Double-Distilled] - 2026-02-11

> (6af424762f)

    Few last tweaks to docs and chat visibility

## [0.0.11 - Bottled] - 2026-02-11

> (478c2416ad)

    Adding emoji and fallback fonts to the AppImage

## [0.0.10 - Bottled] - 2026-02-11

> (8b26de4b74)

    Apply patchelf for Python to point to OS glibc

## [0.0.9 - Bottled] - 2026-02-11

> (7a214f6d96)

    Bump the version

> (d8d396ef22)

    Fixed to Python paths in AppImage builder

> (303781ecb3)

    Refactor emoji picker
    
      Problem (message reactions implementation mostly):
      - Emoji picker dialog contained 147 lines of database logic
      - Direct XMPP calls and account/client dependencies in UI layer
      - Violated separation of concerns (ADR rule)
    
      Changes:
      - Created message_reactions.py: extracted reaction business logic
        to dedicated MessageReactions class (~400 lines)
      - Simplified emoji_picker_dialog.py: 595→351 lines, now pure UI
        that returns Optional[str], zero business logic
      - Updated MessageBarrel: 1160→1057 lines, delegates to reactions
      - Added XMPPAccount.send_reaction() / remove_reaction() public API
      - Updated context_menus.py: handles business logic after picker
      - Updated chat_view.py: handles emoji insertion after picker
      - Added "Remove Reaction" as separate context menu item

> (86b2ec0d11)

    Add TLS client certificate support (passwordless)
    
    Implement support for TLS client certificates using SASL EXTERNAL mechanism.
    Only unencrypted (passwordless) client certificates are supported.
    
    GUI Layer:
    - Add cert_validator.py utility for validating certificates
    - Update account_dialog.py:
      - Add certificate file browser and path input
      - Validate on file selection, test connection, and save
      - Show inline error label and test status icon
    
    Connection Layer:
    - Pass client_cert_path through to DrunkXMPP
    - Add connection_failed event handler for SSL/TLS errors
    - Filter errors: show dialog only for cert/SSL issues, not transient failures
    
    DrunkXMPP:
    - Add client_cert_path parameter to DrunkXMPP constructor
    - Set self.certfile and self.keyfile for slixmpp
    - Add cert_stdin_prevention.py slixmpp patch:
      - Prevents OpenSSL from prompting stdin for passwords
      - Emits connection_failed event on cert loading errors
      - Provides helpful error messages
    
    Database:
    - Bump schema version 12 → 13
    - Add client_cert_path and client_cert_password fields to account table
    - client_cert_password reserved for future use
    
    Error Handling:
    - Use non-blocking QMessageBox.show() to avoid breaking async loop
    - Smart filtering: SSL/cert errors show dialog, network errors logged only
    
    NOTES:
    - Works with both STARTTLS and direct TLS (xmpps)
    - Certificates must be in PEM format with unencrypted private key
    - Validation happens in GUI before connection attempt for immediate feedback

> (ec35ee9a9c)

    Updated contact search to pick MUC names properly

> (e292960a6a)

    Implement roster contact search with dropdown
    
    - Add search dropdown (QListWidget) under roster search box
    - Triggers after 2 characters, searches contact names and JIDs
    - Shows contact type emoji (👤 for contacts, 🏠 for MUCs)
    - Displays account JID for multi-account disambiguation
    - Enter key or mouse click opens chat with selected contact
    - Arrow keys navigate results, ESC clears search
    - Event filter prevents Enter/ESC from propagating to main window
    - Traverse widget hierarchy to find MainWindow (parent is QSplitter)
    - Dropdown width: 600px to accommodate long JIDs
    - No result limit (XMPP rosters are typically small)

## [0.0.8 - Bottled] - 2026-02-10

> (52e93b1d32)

    Bumped version

> (c36d688114)

    Fix AppImage forward compatibility and build
    
    - Exclude glibc from bundling to fix Debian 13+ compatibility
      (bundled glibc 2.35 caused GLIBC_PRIVATE symbol errors)
    - Fix pip shebang pollution using /usr/bin/python3 -m pip
    - Remove duplicate file copying from appimage.yml after_bundle
    - Update icon name to match app ID (com.siproxylin)
    - Use system glibc for forward compatibility (requires glibc 2.35+)

> (70d8e9e3bd)

    Fix blocking dialogs causing asyncio errors
    
    Replace all blocking .exec() and .warning() calls with non-blocking
    .show() pattern to prevent "Cannot enter into task while another task
    is being executed" errors during async XMPP operations.
    
    Pattern: dialog.exec() → signal-based handlers + dialog.show()
    
    All dialogs now use signal connections (accepted/rejected/buttonClicked)
    to handle user actions asynchronously.

> (6a7d4cd11c)

    Added MUC membership request (client-side only)
    
    Add ability for users to request membership in members-only rooms via
    XEP-0077 in-band registration (XEP-0045 §7.10).
    
    Changes:
    - Apply xep_0045_membership patch at startup
    - Register xep_0077 plugin in DrunkXMPP
    - Add request_room_membership() wrapper in DrunkXMPP client
    - Fix blocking dialogs in membership request flow (use .show())
    
    Flow: User clicks "Request Membership" → enters nickname/reason →
    request sent to server. Server may auto-approve, queue for admin
    approval, or reject.
    
    Note: Admin notification/approval UI not implemented as the protocol
    is not standardized across servers. Most servers either auto-approve
    or require manual affiliation management.

> (a90f1cc18d)

    Update README

> (f6c975b601)

    MUC details: unify Participants tab
    
    Replace separate "Users Online" and "All Members" tabs with a single
    unified Participants tab featuring checkbox filters (Online/Offline/
    Owners/Admins/Members/Banned/Others).
    
    Changes:
    - Single 5-column table: Nickname | JID | Status | Role | Affiliation
    - Merged data fetching: online participants + all affiliations
    - Context menu adapts: role changes (online only) vs affiliations (both)
    - Color-coded status and affiliations for better visibility
    
    Benefits:
    - Consistent UI/UX for all participant types
    - Single unified view reduces cognitive load

> (a71a89d20b)

    Phase 7#2: Subject editing + disco/config refresh
    
    - Add Edit Subject button (moderators + participants if allowed)
    - Parse allow_subject_change from disco#info (XEP-0128)
    - Track room subjects in real-time
    - Fetch fresh disco#info and config when dialog opens
    - Add Tier 2 config fields (allow_invites, allow_subject_change, whois)
    
    Fixes stale cache issue where config changes weren't reflected
    without restart.

> (0a3e28ea5c)

    Improve MUC join UX
    
    1. Nickname now optional in "Add Group" dialog
       - Uses JID localpart as default
       - User can override by typing custom nickname
    
    2. Added "Show" checkbox to password fields
       - "Add Group" dialog: toggle password visibility
       - Password prompt dialog: toggle password visibility
    
    3. Removed redundant MUC nickname field from Account Settings
       - Previously had separate "MUC Nickname" setting

> (08ab2892ca)

    Fix MUC password prompt not appearing
    
    When joining a password-protected MUC room without a password, the
    password dialog would only appear on the first attempt after app restart.
    Subsequent attempts (leave → join) would get stuck in "Joining..." state.
    
    Root cause: slixmpp's wildcard event handlers (muc::*::presence-error)
    stop working after first invocation - a slixmpp bug where wildcard MUC
    event handlers become inactive.
    
    Solution: Register per-room disposable error handlers dynamically in
    join_room() instead of relying on wildcards. Using disposable=True
    ensures handlers auto-remove after first use, preventing duplicates
    on password retry attempts.
    
    Additional fixes:
    - Move room to self.rooms dict BEFORE join attempt (fixes race condition)
    - Skip auto-rejoin when user explicitly leaves room
    - Remove context check in header.py so password dialogs work for rooms
      not currently displayed
    - Add muc_join_success signal to hide "Joining..." button on success
    
    Also includes: Room configuration UI (XEP-0045 owner settings) - allows
    owners to configure room name, description, password, members-only,
    moderation, max users, persistence, public listing, and message archiving.

> (134c03d6b1)

    Step 7 of main_window.py refactoring
    
    - Created MUCManager: MUC invites, joins, role changes, leaving
    - Extracted on_muc_invite_received, _execute_room_join_from_invite, on_muc_role_changed, leave_muc
    - Total: 62% reduction in size

> (d0d10158fe)

    Step 6 of main_window.py refactoring
    
    - Created RosterManager: roster updates, presence, typing, avatars
    - Size reduced by 54%
    - Modular architecture ready for future development

> (5b25a4d88a)

    Step 5 of main_window.py refactoring
    
    - Created DialogManager: account/contact/room/settings dialogs
    - Total: 49% reduction in size
    - 6 managers extracted: Call, Notification, Menu, Subscription, Message, Dialog

> (d034ffafb5)

    Step 4 of main_window.py refactoring
    
    - Created MessageManager: send/edit messages, file uploads, replies
    - Total: 47% size reduction

> (4c0d18e4b4)

    Step 3 of main_window.py refactoring
    
    - Created SubscriptionManager: subscription dialogs, contacts blocking (XEP-0191)
    - Total: 33% reduction in size

> (42959d0e4a)

    Step 2 of main_window.py refactoring
    
    - Created MenuManager (471 lines): menu bar, font/theme/roster, log viewers
    - Total: 3,335 → 2,424 lines (27% reduction)

> (7fe8c8cbfc)

    Step 1 of main_window.py refactoring (15% reduced)
    
      - Created managers/ directory for subsystems
      - CallManager (446 lines): call windows, dialogs, Go service
      - NotificationManager (212 lines): OS notifications

> (44d5cffa79)

    Voice request real-time throttling, feedback
    
    Polishes Phase 6 voice request feature with production-ready improvements:
    
    1. Real-time Role Change Monitoring (drunk_xmpp/client.py)
    2. Signal Propagation (siproxylin/core/brewery.py, connection.py)
    3. Auto-Update UI on Role Change (siproxylin/gui/main_window.py, chat_view.py)
       - Overlay now disappears instantly when voice granted
       - Overlay reappears with fresh state if voice revoked
    4. Request Throttling - 1 Hour Cooldown (siproxylin/core/barrels/muc.py)
    5. Timer Reset on Role Changes (siproxylin/core/barrels/muc.py, chat_view.py)
    6. User Feedback via Overlay Updates (siproxylin/gui/chat_view/taps/input.py)

> (11516aba69)

    Add voice request feature for moderated MUCs
    
    Implements XEP-0045 voice requests for visitors in moderated rooms.
    When a user is a visitor (no voice), they see an overlay with a
    clickable link to request participant role from moderators.
    
    Components:
    
    1. MUC Barrel API (siproxylin/core/barrels/muc.py)
       - Add request_voice() method wrapping xep_0045.request_voice()
       - Sends voice request to room moderators per XEP-0045 §8.6
    
    2. Visitor Overlay Widget (siproxylin/gui/chat_view/taps/input.py)
       - Add QLabel overlay anchored inside MessageInputField
       - Positioned via resizeEvent() for correct sizing
       - Clickable link emits voice_request_clicked signal
    
    3. Chat View Integration (siproxylin/gui/chat_view/chat_view.py)
       - Add _update_muc_input_state() to check visitor role
       - Connect voice_request_clicked to _handle_voice_request()
       - Call _update_muc_input_state() when loading MUC conversations
    
    4. DrunkXMPP Role Change Detection (drunk_xmpp/client.py)
       - Track role changes in _on_muc_presence handler
       - Add on_muc_role_changed_callback for future real-time updates
       - Log role transitions (visitor → participant, etc.)
    
    Known limitations (polish remaining for next session):
    - No request throttling (can spam requests)
    - No user feedback after sending request
    - Overlay doesn't auto-hide when promoted (requires chat switch)
    - No retry countdown/timer

## [0.0.7 - Bottled] - 2026-02-08

> (2898bcbe7a)

    Fixes to AppImage builder
    
    1. Added patchelf (was missing and binaries kept pointing to OS libs)
    2. Added a step to check for the tool chain
    3. Bumped the version

> (8e2add5b5c)

    Add Delete Chat feature and refactor chat list
    
    PRIVACY-FIRST DESIGN:
    - "Clear History" now uses hard DELETE (not hide=1) - messages truly deleted
    - "Delete Chat" deletes conversation + messages, removes from chat list
    - Both operations irreversible for privacy (no soft-delete lingering data)
    - Kept hide field for future selective hiding feature (e.g., sensitive content)
    
    CHAT LIST vs CONTACT LIST SEPARATION:
    - Left side = "Chat List" showing only conversations with messages
    - Roster contacts without messages not shown (use Contacts menu to start chat)
    - "Delete Chat" → deletes messages → conversation disappears (privacy fixed!)
    - New messages from unknown contacts appear immediately in chat list
    
    DELETE CHAT FEATURE:
    - Added "Delete Chat" button in Contact Details Dialog (red, destructive)
    - Added "Delete Chat" in contact list context menu
    - Deletes conversation row → CASCADE deletes all content_items
    - Immediately removes from UI and refreshes chat list
    
    MENU REORGANIZATION:
    - Created new "Contacts" top-level menu (File, Edit, View, Contacts, Help)
    - Moved from File: "Add Contact", "Add Group"
    - Moved from Edit: "Contacts" (now "Manage Contacts" with Ctrl+Shift+C)
    - Edit → Accounts submenu (cleaner, groups account management)
    
    OPEN CHAT FIX:
    - "Open Chat" from Contacts manager now shows conversation in chat list
    - Added load_roster() after load_conversation() to refresh UI
    - Fixes issue where newly opened chats didn't appear until restart

> (5fdacc8c64)

    MUC join error handle and membership request flow
    
    - Added error callback from DrunkXMPP to MucBarrel to GUI
    - Re-enables join button after error
    - Errors handled: registration-required, forbidden, not-authorized,
      conflict, service-unavailable, item-not-found, not-allowed, jid-malformed
    
    - Created slixmpp patch adding request_room_membership() to XEP-0045
    - Uses XEP-0077 (In-Band Registration) + XEP-0004 (Data Forms)
    - Detects registration-required error and shows membership dialog
    - Nickname editable (pre-filled from account.muc_nickname || nickname || JID)
    - Saves chosen nickname to bookmark after successful request
    
    Contact List Fix: Show Non-Roster Conversations
    - Updated query to show conversations even if JID not in roster
    - Fixes phantom unread count from server messages (e.g., conversations.im)
    - Enables "Delete Chat" feature (see DELETE-CHAT.md for next session)
    
    Test Tool Enhancements
      - Added /pep-nodes, /pep-get, /pep-delete commands
      - Added /pep-subscriptions, /pep-unsubscribe commands
      - Used for OMEMO phantom subscription investigation

> (6846de6372)

    Add MUC join error feedback messages
    
    Implements Phase 4 of MUC features roadmap - provides clear error messages
    when joining MUC rooms fails due to server rejection (members-only, banned,
    password incorrect, etc.).
    
    IMPLEMENTATION:
    
    1. DrunkXMPP Layer (drunk_xmpp/client.py)
       - Added on_muc_join_error_callback parameter to __init__
       - Implemented _on_muc_error() handler for presence_error events
       - Filters for MUC rooms only (checks self.rooms dict)
       - Calls callback with (room_jid, error_condition, error_text)
       - Uses direct await (not asyncio.create_task) following codebase pattern
    
    2. MUC Barrel Layer (siproxylin/core/barrels/muc.py)
       - Added on_muc_join_error() async method
       - Maps 8 XMPP error conditions to user-friendly messages:
         * registration-required → "Membership required to join this room"
         * forbidden → "You are banned from this room"
         * not-authorized → "Password incorrect or authorization failed"
         * conflict → "Nickname already in use"
         * service-unavailable → "Room does not exist or is unavailable"
         * item-not-found → "Room does not exist"
         * not-allowed → "You are not allowed to join this room"
         * jid-malformed → "Invalid room address"
       - Fallback: "Failed to join room" for unknown errors
       - Emits Qt signal directly (thread-safe, no QTimer wrapper needed)
       - Formats server details: "Server message: error-code: server text"
    
    3. Signal Wiring
       - Added muc_join_error signal to XMPPAccount (brewery.py)
       - Added muc_join_error signal to Account stub (barrels/account.py)
       - Registered callback in brewery signal dictionary
       - Wired callback in ConnectionBarrel
    
    4. GUI Layer (siproxylin/gui/chat_view/taps/header.py)
       - Added _on_muc_join_error() slot with proper signal tracking
       - Tracks _muc_error_account_id to disconnect from previous account
       - Stops MUC roster refresh timer on error (prevents 30s timeout)
       - Re-enables join button with "Join Room" text
       - Shows NON-BLOCKING error dialog (critical fix for async contexts)
       - Dialog format: friendly message + server details in parentheses
       - Hides join button properly for 1-1 chats (not MUC)

> (ba8a6b241c)

    MUC join flow and status, affiliation tracking
    
      PHASE 1: Code Deduplication
      - Created _perform_room_join() helper (eliminates 70 lines duplication)
      - Moved config/features fetch to on_muc_joined callback (fixes timing bug)
      - Added auto-refresh on status code 104 (room config changed event)
    
      PHASE 2: Affiliation Tracking
      - Track own affiliation/role per room (owner, admin, member, etc.)
      - Added MucBarrel API: get_own_affiliation(), is_room_owner()
      - Show affiliation in MUC details dialog with emoji icons
      - Disable refresh button for non-owners with helpful tooltip
    
      PHASE 3: Join Status Fix
      - Contact list shows proper joined/not-joined status
      - Chat header shows "Not joined" instead of "Joining..." forever
      - Added green "Join Room" button for bookmarked-but-not-joined rooms
      - Button hides automatically after successful join
    
      KNOWN ISSUE: Members-only room errors not shown to user yet
      - Server correctly rejects non-members with registration-required
      - Error logged but not propagated to UI
      - Next: Add error callback + membership request flow

> (b2e8b4b381)

    MUC room configdisplay with in-memory caching
    
    Implements XEP-0045 room configuration discovery (session-scoped in-memory storage).
    
    Changes:
    - Add get_room_config() to DrunkXMPP for querying owner config form
    - Add in-memory cache in MucBarrel (self.room_configs dict)
    - Auto-fetch config on room join (non-blocking, owner-only)
    - Update RoomInfo dataclass with 4 new fields (max_users, allow_invites,
      allow_subject_change, enable_logging)
    - Update MUC details dialog to display config values or "Unknown"
    - Add /room-config test command for manual testing
    - Fix migration race condition by moving db.initialize() to main.py startup
    
     MUC config auto-refreshed on each app launch when rooms are rejoined.

> (d1cf5ca798)

    Refactor MUC dialogs to use barrel API pattern
    
    - Add MUC service layer with data classes (RoomInfo, RoomSettings, Participant, Bookmark)
    - Add 6 new barrel methods: get_room_info, get_room_settings, update_room_settings,
      get_participants, get_bookmark, create_or_update_bookmark
    - Refactor muc_details_dialog.py: Remove all direct DB access (16 instances)
    - Refactor join_room_dialog.py: Remove all direct DB access
    - Result: Clean separation of concerns (GUI → Barrel → DB), improved testability

> (7d6f86011d)

    Fixed "FIRST-OMEMO-NOT-DELIVERED" bug

> (eafc5b024f)

    Updated .gitignore

> (a36e9a35a3)

    Fixing leftovers for nickname and muc_nickname

> (320d7994e8)

    XEP-0172 nickname publishing + fix ghost messages
    
    Part 1: XEP-0172 User Nickname Publishing
    - Database migration v11→v12: Rename account.alias to account.nickname
    - Add account.muc_nickname field for separate MUC room nickname
    - Implement nickname publishing via plugin['xep_0172'].publish_nick()
    - Auto-publish on connection and reconnection (XEP-0172 requirement)
    - Add publish_nickname() method to DrunkXMPP client
    - Add publish_own_nickname() wrapper in Brewery for GUI access
    - Update AccountDialog: "Alias" → "Nickname (XEP-0172)", add "MUC Nickname"
    - Nickname fallback: muc_nickname → nickname → JID localpart
    - Update all SQL queries across 16 files: alias → nickname
    - Fix roster_name undefined bug in chat_view for MUC conversations
    
    Part 2: Carbon OMEMO Filtering (Ghost Message Fix)
    - Fix ghost "[Failed to decrypt OMEMO message]" entries in conversations
    - Root cause: Carbons of messages encrypted for other recipients were stored
    - Solution: Parse OMEMO header and check device IDs BEFORE decryption
    - Extract recipient device IDs (rid) from <key> elements in OMEMO header
    - Compare with our own device ID from session_manager
    - Skip storing carbon if our device ID is not in recipient list
    - Preserves error visibility: Real OMEMO failures still logged/stored
    - Matches Conversations app behavior (no ghost messages)
    - Applied to both _on_carbon_sent() and _on_carbon_received()

## [0.0.6 - Bottled] - 2026-02-03

> (8a267e0c9f)

    Bump version

> (90967e6065)

    Add XEP-0172 nickname + refactor contact dialogs
    
    Part 1: XEP-0172 User Nickname Support
    - Register XEP-0172 plugin and handle user_nick_publish events
    - Implement 3-source display name priority: roster.name > nickname > JID
    - Store nicknames in-memory (server provides on startup via PEP)
    - Add unified _refresh_contact_display_name() for consistent UI updates
    - Update all UI components (contact list, chat view, header)
    
    Bug fixes:
    - Fix drunk-xmpp.client logger name (was silent due to config mismatch)
    - Add missing on_nickname_update_callback in ConnectionBarrel
    
    Known limitation: Nickname clearing only works after app restart.
    
    Part 2: Contact Details Dialog Refactoring
    - Replace OMEMOKeysDialog with unified ContactDetailsDialog
    - Add typing notifications checkbox to dialog Settings tab
    - Remove typing button from chat header (functionality still works)
    - Connect ContactDetailsDialog to all entry points:
      * Right-click contact → "View Details"
      * Chat header gear button
      * Edit → Contacts → Edit button
    - Delete omemo_keys_dialog.py (730 lines removed)

> (d969a26578)

    Removed the double "v" in the version on welcome page

## [0.0.5 - Bottled] - 2026-02-03

> (86dc7f9dec)

    Fix main.py to respect path mode from parameters

> (163f753708)

    Made call dialog non-modal to not block main window

> (dd462d989f)

    Fixed GH double tagging

> (f29e1711c6)

    Fixing the copying of version.sh into right place to provide build information

> (821d203e2d)

    Permit old codename, just show warning

> (59bc82dbd9)

    Update version number

> (ee1d94dee4)

    Fixed window title names

> (cbb4a7f561)

    Updated docs

> (238e81ad9c)

    Added DrunkXMPP test tool

## [0.0.4 - Bottled] - 2026-02-01

> (f203b730b2)

    Adding caching drunk_call_service Go binary

> (bd4dd66497)

    Update README.md

> (7cebcdd623)

    Update ARCHITECTURE.md

> (2d03adc24e)

    Tweaks to GitHub builder cache

> (f3ea6b2d47)

    Adding proper version handling for builds

## [0.0.3] - 2026-02-01

> (7efa84c008)

    GH release fixes

> (9c0ec2f08a)

    Bump go to 1.24.12 and fix repo parameter

> (000dc0155d)

    Replace deprecated create-release action with gh CLI

> (0421368673)

    Adding APPIMAGE_EXTRACT_AND_RUN=1 to avoide fuse requirement

> (91da8625b2)

    Fix build version variable

> (fd6d1f12a1)

    Replace deprecated create-release action with gh CLI

> (0a07d01cde)

    Fix GOPATH - use /usr/local/go-tools instead of $HOME/go

> (d3b7cfb3e9)

    Add python3-protobuf for gRPC Python bindings

> (d7957d80ab)

    Use Debian container's native Python and manually install Go

> (0bf0ec1541)

    Fix GitHub Actions for Debian container - remove sudo, fix Go cache

> (ed193efbd4)

    Updates to GitHub workflow

> (41ac51073f)

    Update README

> (44a6299602)

    v0.0.3 Bottled - First more or less stable release

