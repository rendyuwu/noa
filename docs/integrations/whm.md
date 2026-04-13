# WHM Integration Reference

This is the canonical WHM integration reference for NOA.
It records the actual NOA admin endpoints, upstream WHM API calls, SSH commands, and
supported WHM features currently used by the implementation.

Update this file whenever WHM-backed features, validation behavior, upstream calls, or SSH
execution paths change.

## Connection base
- WHM API base: `https://<whm-host>:2087`
- WHM auth for examples: `-H 'Authorization: whm <whm-user>:<api-token>'`

## Implemented in NOA today

### NOA admin endpoints currently implemented
- `GET /admin/whm/servers`
- `POST /admin/whm/servers`
- `PATCH /admin/whm/servers/{server_id}`
- `DELETE /admin/whm/servers/{server_id}`
- `POST /admin/whm/servers/{server_id}/validate`

### Browser proxy endpoints

Browser calls are made through the same-origin proxy as:
- `GET /api/admin/whm/servers`
- `POST /api/admin/whm/servers`
- `PATCH /api/admin/whm/servers/{server_id}`
- `DELETE /api/admin/whm/servers/{server_id}`
- `POST /api/admin/whm/servers/{server_id}/validate`

### Upstream WHM API endpoints currently used by code

##### List available applications
```bash
curl -k \
  -H 'Authorization: whm <whm-user>:<api-token>' \
  'https://<whm-host>:2087/json-api/applist?api.version=1'
```

##### List accounts
```bash
curl -k \
  -H 'Authorization: whm <whm-user>:<api-token>' \
  'https://<whm-host>:2087/json-api/listaccts?api.version=1'
```

##### Suspend account
```bash
curl -k \
  -H 'Authorization: whm <whm-user>:<api-token>' \
  'https://<whm-host>:2087/json-api/suspendacct?api.version=1&user=<cpanel-user>&reason=<reason>'
```

##### Unsuspend account
```bash
curl -k \
  -H 'Authorization: whm <whm-user>:<api-token>' \
  'https://<whm-host>:2087/json-api/unsuspendacct?api.version=1&user=<cpanel-user>'
```

##### Change contact email
```bash
curl -k \
  -H 'Authorization: whm <whm-user>:<api-token>' \
  'https://<whm-host>:2087/json-api/modifyacct?api.version=1&user=<cpanel-user>&contactemail=<new-email>'
```

##### Change primary domain
```bash
curl -k \
  -H 'Authorization: whm <whm-user>:<api-token>' \
  'https://<whm-host>:2087/json-api/modifyacct?api.version=1&user=<cpanel-user>&domain=<new-domain>'
```

##### Get domain owner
```bash
curl -k \
  -H 'Authorization: whm <whm-user>:<api-token>' \
  'https://<whm-host>:2087/json-api/getdomainowner?api.version=1&domain=<domain>'
```

##### List account domains through UAPI bridge
```bash
curl -k \
  -H 'Authorization: whm <whm-user>:<api-token>' \
  'https://<whm-host>:2087/json-api/uapi_cpanel?api.version=1&cpanel.user=<cpanel-user>&cpanel.module=DomainInfo&cpanel.function=list_domains'
```

##### Legacy CSF-over-WHM-token path still present in client
```bash
curl -k \
  -H 'Authorization: whm <whm-user>:<api-token>' \
  'https://<whm-host>:2087/cgi/addon_csf.cgi'
```

### SSH commands currently used by code

##### SSH connectivity probe used during WHM server validation
```bash
true
```

##### Generic binary existence check
```bash
command -v <binary-name>
```

##### Check Imunify binary
```bash
command -v imunify360-agent
```

##### Check CSF binary
```bash
command -v /usr/sbin/csf
```

##### CSF command wrapper
```bash
TERM=dumb /usr/sbin/csf <args...>
```

Implemented CSF argument patterns:
- `-g <target>`
- `-tr <target>`
- `-dr <target>`
- `-ta <target> <duration_seconds> <reason>`
- `-tra <target>`
- `-ar <target>`
- `-td <target> <duration_seconds> <reason>`

##### Imunify360 command wrapper
```bash
imunify360-agent <args...>
```

Implemented Imunify argument patterns:
- `ip-list local list --by-ip <target> --json`
- `ip-list local delete --purpose drop <target> --json`
- `ip-list local add --purpose white <target> --comment <reason> --expiration <unix-epoch> --json`
- `ip-list local delete --purpose white <target> --json`
- `ip-list local add --purpose drop <target> --comment <reason> --expiration <unix-epoch> --json`

##### Mail log failed-auth investigation command
Code builds and runs a quoted `bash -lc` pipeline with this shape:

```bash
bash -lc 'set -o pipefail; shopt -s nullglob; files=(/var/log/maillog* [/var/log/exim_mainlog* when smtpauth]); if [ ${#files[@]} -eq 0 ]; then echo "No matching mail log files found" >&2; exit 3; fi; zgrep -h -E <date-anchor> "${files[@]}" | <grep-ip-match> | grep -iE <failed-auth-regex> | awk <awk-program> | sort | uniq -c | sort -nr'
```

Core pipeline parts implemented:
- `zgrep -h -E <date-anchor> "${files[@]}"`
- `grep -E ...` for IPv4 or `grep -F ...` for IPv6
- `grep -iE 'Failed|auth failed|Authentication failed|authenticator failed|password mismatch'`
- `awk ...`
- `sort | uniq -c | sort -nr`

## Current WHM features actually implemented
- WHM server inventory CRUD in NOA admin UI/API
- WHM server validation
- Account suspend / unsuspend
- Contact email change
- Primary domain change
- Domain owner lookup
- Domain list lookup via WHM UAPI bridge
- Firewall/IP operations via SSH using CSF and Imunify360
- Binary existence checks over SSH
- Mail log failed-auth username suspect analysis over SSH

## Backlog / not yet implemented
- No separate WHM research backlog has been added to this file yet.
