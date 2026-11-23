"""Create a new mode-0600 environment without displaying credentials."""
import os
import secrets
from pathlib import Path
p=Path('.env')
if p.exists() and p.stat().st_size:
    raise SystemExit('Existing nonempty .env preserved')
text=Path('.env.example').read_text().replace('WORKER_API_TOKEN=\n','WORKER_API_TOKEN='+secrets.token_urlsafe(48)+'\n')
fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as out:
    out.write(text)
os.chmod(p,0o600)
print('Created local .env with strong token; values suppressed')
