# Clerk setup for LakeStream

LakeStream's auth provider is controlled by `AUTH_PROVIDER` (`legacy` |
`clerk`). This doc covers the one-time Clerk dashboard setup and the env
wiring. Rollback at any point before legacy removal is a pure env flip back to
`AUTH_PROVIDER=legacy`.

## 1. Create the Clerk application

Use a **separate Clerk application for LakeStream** — do not reuse the
ChampUTM/ChampLens apps. LakeStream imports users with bcrypt hashes, uses
Clerk Organizations, and pins custom `publicMetadata` (`role`, `org_id`);
sharing an app would leak those users and conventions into other tools'
user pools.

1. dashboard.clerk.com → Create application. Sign-in methods: **Email +
   Google** (Champions Group default for internal tools).
2. **Enable Organizations** (Configure → Organizations).
3. Sessions → Customize session token — add the claims LakeStream reads:

   ```json
   {
     "email": "{{user.primary_email_address}}",
     "public_metadata": "{{user.public_metadata}}"
   }
   ```

   The active organization arrives automatically in v2 session tokens as the
   compact `o` claim (`{id, rol, slg}`); older v1 templates surface
   `org_id`/`org_role`/`org_slug`. LakeStream reads both shapes.
4. Paths/redirects: after sign-in and after sign-out → `/`.

## 2. Environment variables

From the app's **API keys** page, fill these in `.env` (never commit values):

```
AUTH_PROVIDER=clerk
ENABLE_LEGACY_JWT=true            # keep true during migration; false after cutover soak
CLERK_SECRET_KEY=sk_...
CLERK_PUBLISHABLE_KEY=pk_...
CLERK_JWKS_URL=https://<subdomain>.clerk.accounts.dev/.well-known/jwks.json
CLERK_ISSUER=https://<subdomain>.clerk.accounts.dev
CLERK_DOMAIN=<subdomain>.clerk.accounts.dev
CLERK_WEBHOOK_SIGNING_SECRET=whsec_...   # from step 3
CLERK_LINK_BY_EMAIL=false         # leave false; the import script links deliberately
```

Deploy targets need the same variables set in their dashboard, then a
redeploy — env changes don't apply to existing builds.

## 3. Webhook endpoint

Clerk dashboard → Webhooks → Add endpoint:

- URL: `https://<your-host>/api/webhooks/clerk`
- Events: `user.created`, `user.updated`, `user.deleted`,
  `organization.created`, `organization.updated`, `organization.deleted`,
  `organizationMembership.created`, `organizationMembership.updated`,
  `organizationMembership.deleted`
- Copy the signing secret into `CLERK_WEBHOOK_SIGNING_SECRET`.

Deliveries are Svix-signed; the endpoint 503s until the secret is set and
400s on any signature mismatch. Handlers are idempotent — Svix retries are
safe, and a user/org the webhook hasn't delivered yet is lazy-provisioned on
their first authenticated request anyway.

## 4. Roles and admin

- `super_admin` is granted ONLY via Clerk **publicMetadata**:
  `{"role": "super_admin"}` on the user. Nothing else makes an admin; the
  legacy `ADMIN_PASSWORD` boot seeding is disabled under Clerk.
- Clerk org role `admin` maps to local `org_owner`; everything else maps to
  `member`.
- Org resolution order per request: active Clerk Organization in the session
  token → `publicMetadata.org_id` (local org UUID, set by the import script)
  → the `default` org.

## 5. Machine access (unchanged by Clerk)

API keys (`ls_...`, sent as `X-API-Key`) are independent of the auth
provider: the Chrome extension, CLI (`lakestream auth login --api-key`), and
MCP HTTP transport all use them. Keys are minted in the web UI under
Settings → API Keys.
