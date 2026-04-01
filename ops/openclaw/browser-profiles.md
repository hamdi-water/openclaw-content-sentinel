# Browser Profiles

Use one dedicated OpenClaw browser profile per social platform:

- `openclaw-linkedin`
- `openclaw-facebook`
- `openclaw-x`

Rules:

- Log in manually with sandbox or approved operator accounts.
- Do not share these profiles with personal browsing.
- Reuse the same profile on every automated run.
- If the site shows a login challenge or suspicious-activity page, stop and require manual intervention.

The local CLI returns the expected profile name through `publish` and `publish-all`.

Quick login bootstrap:

```powershell
powershell -ExecutionPolicy Bypass -File .\ops\openclaw\open-social-logins.ps1
```

Expected manual state after login:

- LinkedIn profile `openclaw-linkedin` reaches the feed or post composer.
- Facebook profile `openclaw-facebook` reaches the home feed or page composer.
- X profile `openclaw-x` reaches the home timeline or post composer.
