#!/usr/bin/env bash
# Quick post-deployment checks. Run on the server:  sudo bash verify.sh media.example.com
set -u   # no pipefail: "grep -q" exits early and would make passing checks look failed
DOMAIN="${1:-}"
ok(){ printf '  [ OK ] %s\n' "$1"; }
bad(){ printf '  [FAIL] %s\n' "$1"; FAILED=1; }
FAILED=0

echo "Containers"
for c in jellyfin caddy; do
  S="$(docker inspect -f '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}n-a{{end}}' "$c" 2>/dev/null || echo missing)"
  [[ "$S" == running/healthy || "$S" == running/n-a ]] && ok "$c: $S" || bad "$c: $S"
done

echo "Exposure (only 22, 80, 443 should listen publicly; 8096 must NOT)"
LISTEN="$(ss -tlnH | awk '{print $4}')"
echo "$LISTEN" | grep -qE '(^|[:.])8096$' && bad "port 8096 is listening on the host" || ok "8096 not published"
ufw status | grep -q 'Status: active' && ok "ufw active" || bad "ufw inactive"

echo "SSH"
sshd -T 2>/dev/null | grep -q '^passwordauthentication no' && ok "password login disabled" || bad "password login still enabled"
sshd -T 2>/dev/null | grep -q '^permitrootlogin no' && ok "root login disabled" || bad "root login allowed"

echo "Storage"
mountpoint -q /srv/media && ok "/srv/media is a mount ($(df -h --output=size,avail /srv/media | tail -n1 | xargs))" || bad "/srv/media is not a separate mount"

if [[ -n "$DOMAIN" ]]; then
  echo "HTTPS ($DOMAIN)"
  CODE="$(curl -s -o /dev/null -w '%{http_code}' "https://$DOMAIN/health" || true)"
  [[ "$CODE" == 200 ]] && ok "https://$DOMAIN/health -> 200" || bad "health returned $CODE"
  END="$(echo | openssl s_client -servername "$DOMAIN" -connect "$DOMAIN:443" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)"
  [[ -n "$END" ]] && ok "certificate valid until $END" || bad "could not read certificate"
  curl -s -o /dev/null -w '%{http_code}' -m 5 "http://$DOMAIN:8096/" | grep -qE '^(000)$' && ok "http :8096 unreachable from outside" || bad "port 8096 answers over plain HTTP"
fi

exit $FAILED
