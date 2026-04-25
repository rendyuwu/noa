# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest `master` | Yes |
| Older commits | No |

## Reporting a Vulnerability

**Do not open a public issue for security vulnerabilities.**

### How to Report

1. **Email:** Send details to **github@rendy.dev**
2. **GitHub:** Use [GitHub's private security advisory](https://github.com/rendyuwu/noa/security/advisories/new)

### What to Include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### Response Timeline

| Stage | Timeline |
|-------|----------|
| Acknowledgment | Within 48 hours |
| Initial assessment | Within 5 business days |
| Fix (Critical) | 24-48 hours |
| Fix (High) | 1 week |
| Fix (Medium/Low) | Next release cycle |

### Disclosure Policy

We follow coordinated disclosure:

1. Reporter submits vulnerability privately
2. We acknowledge and assess
3. We develop and test a fix
4. We release the fix
5. We publicly disclose with credit to the reporter (unless anonymity requested)

## Security Architecture

| Area | Location | Notes |
|------|----------|-------|
| Authentication | `apps/api/src/noa_api/auth/` | LDAP bind + JWT in httpOnly cookie |
| Authorization | `apps/api/src/noa_api/core/authorization/` | RBAC with role-based tool permissions |
| Input sanitization | Tool parameter validation | Whitespace-only rejection, format validation |
| Secrets at rest | `apps/api/src/noa_api/core/crypto/` | Fernet encryption for API tokens, SSH keys |
| CHANGE tool gates | `apps/api/src/noa_api/core/agent/` | Approval required with recorded reason |
| Proxy boundary | `apps/web/src/app/api/` | Browser never calls FastAPI directly |

## Security-Sensitive Areas

Changes to these areas require extra review:

- Authentication middleware and JWT handling
- RBAC permission checks and admin guards
- Tool execution and approval flow
- Secret encryption/decryption
- Proxy route handlers
- Database migration scripts
