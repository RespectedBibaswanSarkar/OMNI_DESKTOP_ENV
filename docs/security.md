# Security model

- Filesystem actions are restricted to a configured workspace root.
- Disallowed actions are rejected at validation time.
- Web requests are bound to localhost by default.
- The dashboard is a local interface only.
- No arbitrary shell construction is permitted.
