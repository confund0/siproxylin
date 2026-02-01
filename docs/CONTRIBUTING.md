# Contributing to Siproxylin

Thank you for your interest in contributing! Siproxylin is a privacy-focused XMPP client, and we welcome contributions that align with our values of privacy, security, and user freedom.

## Code of Conduct

- Be respectful and professional
- Focus on technical merit and user benefit
- Provide constructive feedback
- Help others learn and grow

## Before You Start

### Read the Documentation

- **Architecture**: `docs/ARCHITECTURE.md` - Understand the system design
- **Build Instructions**: `docs/BUILD.md` - Set up your development environment
- **Architecture Decision Records**: `docs/ADR.md` - Learn the "10 Commandments" (critical rules)
- **Technology Stack**: `docs/TECHNOLOGY.md` - Dependencies and tools

### Development Environment

**Prerequisites**:
- Python 3.11+
- Go 1.21+ (for call service)
- GStreamer 1.0
- Qt6 libraries
- Git

**Setup**:
```bash
# Clone repository
git clone https://github.com/yourusername/siproxylin.git
cd siproxylin

# Install Python dependencies
pip install -r requirements.txt

# Build Go call service
cd drunk_call_service
go build -o ../drunk_call_service.bin
cd ..

# Run application in dev mode
python main.py
```

**Development Data**: Dev mode stores all data in `./app_dev_paths/` (git-ignored)

## Development Workflow

### 1. Pick or Create an Issue

- Check [Issues](https://github.com/yourusername/siproxylin/issues) for open tasks
- Comment on the issue to claim it
- For new features, create an issue first to discuss approach

### 2. Create a Feature Branch

```bash
git checkout -b feature/your-feature-name
# or
git checkout -b fix/bug-description
```

**Branch Naming**:
- `feature/` - New functionality
- `fix/` - Bug fixes
- `refactor/` - Code improvements
- `docs/` - Documentation updates

### 3. Follow the Architectural Rules

**The 10 Commandments** (from `docs/ADR.md`):

1. **DrunkXMPP is STABLE**: Test changes in `tests/test-drunk-xmpp.py` first
2. **Use Library Methods**: Never manually parse XML (use slixmpp APIs)
3. **Use Logger**: Always use logger instances, never `print()`
4. **Database Singleton**: ALWAYS use `get_db()`, never create new connections
5. **Message States**: Let handlers update `marked` field automatically
6. **Async Callbacks**: All DrunkXMPP callbacks must be `async`
7. **Quality Over Speed**: Take time to get it right
8. **No Hardcoded Values**: Parse codec parameters from SDP, don't hardcode

### 4. Write Clean Code

**Code Style**:
- Follow PEP 8 for Python code
- Use type hints where appropriate
- Write docstrings for public methods
- Keep functions focused and small

**Error Handling**:
```python
# Good
try:
    result = await xmpp_operation()
except XMPPError as e:
    logger.error(f"XMPP operation failed: {e}", exc_info=True)
    # Handle gracefully
except Exception as e:
    logger.error(f"Unexpected error: {e}", exc_info=True)
    # Don't crash

# Bad
try:
    result = await xmpp_operation()
except:  # Too broad, no logging
    pass
```

**Logging**:
```python
# Good
from app.utils.logger import setup_account_logger
logger = setup_account_logger(account_id)
logger.info("Connection established")
logger.error("Failed to send message", exc_info=True)

# Bad
print("Connection established")  # Never use print()
```

### 5. Test Your Changes

**Manual Testing**:
- Test with at least two XMPP accounts
- Test both encrypted (OMEMO) and unencrypted scenarios
- Test with different servers (ejabberd, Prosody, Conversations.im)
- Test edge cases (offline messages, reconnections, etc.)

**Automated Tests** (when available):
```bash
# Run DrunkXMPP tests
python tests/test-drunk-xmpp.py

# Run unit tests
pytest tests/
```

**Test Account Setup**:
- Create test accounts on public XMPP servers
- Use conversations.im, 404.city, or run your own server
- Never commit real credentials

### 6. Database Changes

**Migrations**:
```bash
# Create new migration
cd app/db/migrations/
touch 010_add_feature.sql

# Write SQL
-- app/db/migrations/010_add_feature.sql
CREATE TABLE new_table (
    id INTEGER PRIMARY KEY,
    data TEXT
);

-- Update version
UPDATE db_version SET version = 10;
```

**Testing Migrations**:
- Delete `app_dev_paths/data/siproxylin.db`
- Run app to create fresh database
- Verify all migrations apply correctly

### 7. Commit Guidelines

**Commit Messages**:
```
<type>: <short description>

<detailed explanation if needed>

Fixes #123
```

**Types**:
- `feat:` - New feature
- `fix:` - Bug fix
- `refactor:` - Code improvement without behavior change
- `docs:` - Documentation only
- `test:` - Test additions or fixes
- `chore:` - Build process, dependencies, etc.

**Examples**:
```
feat: Add spell checking to message input

Implements XEP-0xxx spell checking with PyEnchant.
Adds per-conversation setting to enable/disable.

Fixes #456

---

fix: Prevent duplicate messages on reconnect

Use stanza-id and origin-id for deduplication.
Handles MAM sync, carbons, and reflections.

Fixes #789
```

**Commit Frequency**:
- Commit at logical checkpoints
- Separate commits for: features, bugfixes, refactoring, documentation
- Keep commits focused (one logical change per commit)

### 8. Submit Pull Request

**Before Submitting**:
- [ ] Code follows the 10 Commandments
- [ ] All files use logger (no `print()` statements)
- [ ] Database uses `get_db()` singleton
- [ ] Changes are tested manually
- [ ] Commit messages are descriptive
- [ ] No sensitive data in commits

**PR Description Template**:
```markdown
## Summary
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Refactoring
- [ ] Documentation

## Testing
How to test these changes:
1. Step one
2. Step two
3. Expected result

## Screenshots (if GUI changes)
[Attach screenshots]

## Checklist
- [ ] Follows architectural rules (ADR.md)
- [ ] Uses logger (no print())
- [ ] Uses get_db() for database
- [ ] Tested with 2+ accounts
- [ ] No hardcoded values
```

**PR Process**:
1. Push your branch to GitHub
2. Create Pull Request against `main` branch
3. Respond to review feedback
4. Make requested changes
5. Maintainer will merge when approved

## Specific Contribution Areas

### Adding a New XEP

1. Check if already implemented (see `app/version.py::SUPPORTED_XEPS`)
2. Implement in DrunkXMPP library (`drunk_xmpp/`)
3. Add callback handling in appropriate barrel
4. Update GUI if needed
5. Add to `SUPPORTED_XEPS` list
6. Document in `docs/PHASE-CHATS/` or appropriate directory

### GUI Improvements

1. Use PySide6 (Qt6) widgets
2. Follow existing patterns (see `app/gui/`)
3. Use Qt signals for async communication
4. Test on different screen sizes
5. Consider accessibility (keyboard navigation, screen readers)

### DrunkXMPP Library Changes

**IMPORTANT**: DrunkXMPP is considered STABLE

1. Discuss changes in issue first
2. Test in `tests/test-drunk-xmpp.py` before GUI integration
3. Maintain backward compatibility
4. Get explicit approval before modifying

### Performance Improvements

- Profile before optimizing (don't guess)
- Measure impact (provide benchmarks)
- Don't sacrifice readability for minor gains
- Focus on database queries and network I/O

### Documentation

- Technical docs go in `docs/`
- User-facing docs go in `README.md` or wiki
- Keep code comments concise
- Prefer self-documenting code over comments

## Communication

### Reporting Bugs

Create an issue with:
- **Summary**: Brief description
- **Steps to Reproduce**: 1, 2, 3...
- **Expected Behavior**: What should happen
- **Actual Behavior**: What actually happens
- **Environment**: OS, Python version, XMPP server
- **Logs**: Relevant log excerpts (redact JIDs/passwords!)

**Log Locations**:
- Dev mode: `app_dev_paths/logs/`
- XDG mode: `~/.local/share/siproxylin/logs/`
- Dot mode: `~/.siproxylin/logs/`

### Feature Requests

Create an issue with:
- **Use Case**: Why is this needed?
- **Proposed Solution**: How should it work?
- **Alternatives**: Other ways to solve the problem
- **XEP Reference**: If XMPP-related, link to XEP

### Questions

- Check documentation first
- Search existing issues
- Ask in GitHub Discussions (if enabled)
- Be specific and provide context

## License

By contributing, you agree that your contributions will be licensed under the **AGPL-3.0 License**.

All contributions must be your original work or properly attributed.

## Recognition

Contributors will be:
- Listed in `CONTRIBUTORS.md`
- Mentioned in release notes for significant contributions
- Credited in commit history

## Getting Help

**Stuck?**
- Read `docs/ADR.md` for architectural guidance
- Check `docs/ARCHITECTURE.md` for system overview
- Look at existing code for patterns
- Ask questions in issue comments

**Common Pitfalls**:
- Forgetting to use `get_db()` singleton
- Using `print()` instead of logger
- Manually parsing XML instead of using slixmpp
- Making DrunkXMPP changes without testing first

## Quality Standards

**We Value**:
- ✅ Correctness over speed
- ✅ Privacy and security
- ✅ Clean, maintainable code
- ✅ Proper error handling
- ✅ Comprehensive testing

**We Reject**:
- ❌ Tracking or telemetry
- ❌ Proprietary dependencies
- ❌ Breaking changes without migration path
- ❌ Undocumented hacks
- ❌ Security vulnerabilities

## Release Process

**For Maintainers**:
1. Update version in `app/version.py`
2. Update `SUPPORTED_XEPS` if needed
3. Build AppImage: `./build-appimage.sh`
4. Create git tag: `git tag v1.2.3`
5. Push tag: `git push origin v1.2.3`
6. Create GitHub release with AppImage attachment
7. Update documentation

## Thank You!

Your contributions make Siproxylin better for everyone. Whether it's code, documentation, bug reports, or feature ideas, we appreciate your effort and time.

**Happy Hacking!** 🍺

---

**Last Updated**: 2026-02-01
**Questions?** Open an issue or check the docs.
