# auth-specs.md

# Goal
Family members authenticate to use the app; each may edit only their own
entries; family-wide viewing allowed.

# Constraints
- Local auth only — no external IdP.
- Stdlib-only credential handling; no new dependencies.
- Credentials stored in the app's sqlite DB.

# Acceptance Criteria

## Users
- `users` table; one row per member from `members.yaml` (grouped by `family_id`).
- Importer CLI seeds users on first run; no password ⇒ cannot log in.
- Passwords only via `impex.py auth set-password --user X`. First password is a
  documented bootstrap (no prior auth); later changes require the current
  password and revoke all sessions for that user.

## Authentication — dashboard
- Passwords hashed with PBKDF2-HMAC-SHA256 (stdlib, random salt, >=600k iters),
  constant-time compare; never stored plaintext/reversibly.
- Successful login issues an opaque random token (>=256 bits); only its SHA-256
  digest is stored server-side (`auth_tokens`: user, issued-at, expiry, revoked).
- Raw token delivered as cookie (SameSite=Lax, Path=/, Max-Age = TTL; default
  10 minutes, configurable in `settings`). Cookie bridged via a minimal same-site
  JS helper (Streamlit has no public cookie API).
- Cookie is the identity source: read at each render, validated against
  `auth_tokens`, restores user into `st.session_state`; else login challenge.
- Logout revokes the token row and clears the cookie; expiry/revocation/absence
  re-presents the login challenge.
- Login failures are generic (never reveal username existence); tokens/salts
  never appear in logs, exports, or git.

## Authentication — CLI
- Every import/export authenticates `--user` before reading (exports) or writing
  (imports).
- Password interactively by default; `--password-file <path>` or `--password-stdin`
  for automation; never via argv/env.
- Auth failure: exit non-zero, no data changes, logged.
- Imports are owned by the authenticated `--user`.

## Authorization
- Any authenticated user may view the whole family's entries + investments.
- Create/update allowed only when the entry/investment owner == current user.
- Dashboard has a "Family / me" toggle; "me" shows/edits only own rows.

## Edit surface (v2)
- In-app editing (Me scope only; Family is read-only):
  - Re-classify any row's kind + category via dropdown tables (Review = UNKNOWN
    rows; Transactions = the filtered set).
  - Create a category (label + kind) on the Categories screen.
  - Update a holding's units / purchase date / purchase NAV / latest date /
    latest NAV / value at cost / value latest — never the primary-key identity.
- No free-form entry/delete yet.

# Out of scope
- Roles/admin users; OAuth/SSO/2FA; self-service password reset or in-app sign-up
- Login rate limiting/lockout; HTTPS/TLS termination (proxy concern)
- Stateless tokens (JWT)