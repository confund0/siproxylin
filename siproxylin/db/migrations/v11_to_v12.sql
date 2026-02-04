-- Migration v11 to v12: Rename alias to nickname and add muc_nickname
--
-- Background: Implementing XEP-0172 User Nickname publishing
--
-- Changes:
-- - Rename 'alias' field to 'nickname' (will be published via XEP-0172)
-- - Add 'muc_nickname' field for MUC room joins (separate from published nickname)
--
-- Fallback logic: muc_nickname || nickname || JID localpart

-- Rename alias field to nickname (for XEP-0172 publishing)
ALTER TABLE account RENAME COLUMN alias TO nickname;

-- Add muc_nickname field for MUC room joins
ALTER TABLE account ADD COLUMN muc_nickname TEXT;
